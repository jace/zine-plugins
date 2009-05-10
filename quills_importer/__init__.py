# -*- coding: utf-8 -*-
import os.path
import xmlrpclib
from time import strptime
from datetime import datetime
from pytz import UTC
from urllib2 import urlparse
from werkzeug import escape
from zine.api import *
from zine.importers import Importer, Blog, Tag, Category, Author, Post, Comment
from zine.i18n import get_timezone
from zine.utils import forms, log
from zine.utils.validators import check, ValidationError
from zine.utils.admin import flash
from zine.utils.http import redirect_to
from zine.utils.text import gen_slug, gen_timestamped_slug
from zine.models import COMMENT_MODERATED, STATUS_PUBLISHED, STATUS_DRAFT
import zine.models

try:
    from pygments import highlight
    from pygments.lexers import get_lexer_by_name
    from pygments.formatters import HtmlFormatter
    have_pygments = True
except ImportError:
    have_pygments = False

__version__ = '0.1'

TEMPLATES = os.path.join(os.path.dirname(__file__), 'templates')

PLONE_STATUS = {
    'published': STATUS_PUBLISHED,
    'draft': STATUS_DRAFT
    }

PLONE_PARSERS = {
    'text/plain': 'text',
    'text/html': 'zeml', # Need ZEML to insert intro tag, if required
    'text/x-rst': 'restructuredtext',
    'text/structured': 'restructuredtext', # Not rst, but similar enough
    }

EXPORTSCRIPT = '''\
# Get all Quills weblog entries on site

catalog = context.portal_catalog
dtool = context.portal_discussion

result = []
for item in catalog(Type=['Weblog Entry']):
    entry = item.getObject()
    replies = []
    if dtool.isDiscussionAllowedFor(entry):
        objreplies = dtool.getDiscussionFor(entry)
        for rid in objreplies.objectIds():
            reply = objreplies.getReply(rid)
            parent = reply.inReplyTo()
            if parent.meta_type == 'Discussion Item':
                parent = parent.id
            else:
                parent = None
            replies.append(dict(
                id=rid,
                title=reply.Title(),
                author=reply.Creator(),
                date=reply.ModificationDate(),
                body=reply.text,
                parent=parent
                ))
    result.append(dict(
        id=entry.id,
        type=entry.Type(),
        url=item.getURL(),
        title=entry.Title(),
        description=entry.Description(),
        body=entry.text(),
        date=entry.EffectiveDate(),
        status=item.review_state,
        format=entry.text.getContentType(),
        description_format=entry.description.getContentType(),
        tags=entry.Subject(),
        allow_comments=entry.isDiscussable(),
        author=entry.Creator(),
        replies=replies,
        ))

return result
'''


def reunicode(value):
    if isinstance(value, str):
        return unicode(value, 'utf-8')
    else:
        return value


def parse_plone_date(value):
    if value == 'None' or not value:
        return UTC.localize(datetime.utcnow()).astimezone(get_timezone())
    else:
        return get_timezone().localize(
            datetime(*(strptime(value, '%Y-%m-%d %H:%M:%S')[:6])))


def is_valid_plone_password(message=None):
    """
    Validates Plone password. Our handler requires that the password not have
    ':' or '@' in it.

    >>> check(is_valid_plone_password, 'mypass')
    True
    >>> check(is_valid_plone_password, 's#c$3')
    True
    >>> check(is_valid_plone_password, 'hi:h0')
    False
    >>> check(is_valid_plone_password, 'hi@there')
    False
    """
    if message is None:
        message = lazy_gettext(u'This password cannot be used.')
    def validator(form, value):
        if value.find(':') != -1 or value.find('@') != -1:
            raise ValidationError(message)
    return validator


def is_valid_blog_url(message=None):
    """
    Validates blog URL. Must begin with http or https and not have a fragment.
    Ignores the message parameter.
    
    >>> check(is_valid_blog_url, 'http://jace.seacrow.com')
    True
    >>> check(is_valid_blog_url, 'http://try:me@jace.seacrow.com/')
    False
    >>> check(is_valid_blog_url, 'http://jace.seacrow.com?start=10')
    False
    >>> check(is_valid_blog_url, 'ftp://jace.seacrow.com/')
    False
    >>> check(is_valid_blog_url, 'http://jace.seacrow.com/#there')
    False
    """
    def validator(form, value):
        parts = urlparse.urlsplit(value)
        if parts.scheme not in ['http', 'https']:
            raise ValidationError(lazy_gettext(u'URLs must be of type '\
                                               u'http or https.'))
        elif parts.fragment:
            raise ValidationError(lazy_gettext(u'URLs may not have a '\
                                               u'#reference part.'))
        elif parts.netloc.find('@') != -1:
            raise ValidationError(lazy_gettext(u'URLs should not be specified '\
                                               u'with username and password.'))
        elif parts.query:
            raise ValidationError(lazy_gettext(u'URLs may not have a ?query.'))
    return validator


class QuillsImportForm(forms.Form):
    """This form asks the user for the Quills blog URL and authorisation."""
    blogurl =  forms.TextField(lazy_gettext(u'Quills Blog URL'),
                               validators=[is_valid_blog_url()],
                               required=True)
    username = forms.TextField(lazy_gettext(u'Plone login'),
                               help_text=lazy_gettext(u'Login and password '\
                               u'required only if you’d like to download '\
                               u'drafts and protected items that are not'\
                               u'visible to the public.'),
                               required=False)
    password = forms.TextField(lazy_gettext(u'Plone password'),
                               required=False,
                               widget=forms.PasswordInput,
                               validators=[is_valid_plone_password()])


class QuillsImporter(Importer):
    name  = u'quills'
    title = u'Quills'

    def import_quills(self, blogurl, username, password):
        """Import from Quills using Zope's XML-RPC interface."""
        yield _(u'<p>Beginning Quills import. Attempting to get data...</p>')
        urlparts = urlparse.urlsplit(blogurl)
        urlnetloc = urlparts.netloc
        urlpath = urlparts.path
        if not urlpath.endswith('/'):
            urlpath += '/' # Trailing slash required for XML-RPC
        if username:
            #: We're using simple HTTP auth, which isn't the smartest thing to
            #: do, but Plone's default cookie-auth system is just a base64
            #: encoding of username:password, which isn't any better. Quills
            #: runs on Plone 2.1 and 2.5, neither of which shipped with a more
            #: secure auth mechanism, so we'll just go with what works. HTTP
            #: auth fallback has been supported by every Zope 2.x release.
            urlnetloc = '%s:%s@%s' % (username, password, urlnetloc)
        useblogurl = urlparse.urlunsplit((urlparts.scheme, urlnetloc, urlpath,
                                          '', ''))
        conn = xmlrpclib.ServerProxy(useblogurl)
        title = conn.Title()
        data = conn.zine_export()
        yield _(u'<p>Got data. Parsing for weblog entries and replies.</p>')

        tags = {}
        posts = {}
        authors = {}

        yield _(u'<ol>')
        for entry in data:
            itemtags = []
            for tag in entry['tags']:
                if tag in tags:
                    itemtags.append(tags[tag])
                else:
                    newtag = Tag(gen_slug(tag), tag)
                    tags[tag] = newtag
                    itemtags.append(newtag)
            if entry['author'] in authors:
                author = authors[entry['author']]
            else:
                author = Author(entry['author'], '', '')
                authors[entry['author']] = author
            status = PLONE_STATUS.get(entry['status'], STATUS_PUBLISHED)
            body = reunicode(entry['body'])
            description = reunicode(entry['description'])
            subject = reunicode(entry['title'])
            parser = PLONE_PARSERS.get(entry['format'], 'zeml')
            pub_date = parse_plone_date(entry['date'])

            if description:
                #: Assume description is text/plain. Anything else is unlikely
                if parser in ['zeml', 'html']:
                    body = u'<intro><p>%s</p></intro>%s' % (description, body)
                else:
                    # We don't know how this parser works, so just insert
                    # description before body, with a blank line in between
                    body = u'%s\n\n%s' % (description, body)

            comments = {}

            for comment in entry['replies']:
                c_body = reunicode(comment['body'])
                c_author = comment['author']
                if c_author in authors:
                    c_author = authors[c_author]
                #: Fix for Jace's anon comments hack
                elif c_author.startswith('!'):
                    c_author = c_author[1:]
                c_body = reunicode(comment['body'])
                c_subject = reunicode(comment['title'])
                if c_subject:
                    c_body = '%s\n\n%s' % (c_subject, c_body)

                comments[comment['id']] = Comment(
                    author = c_author,
                    body = c_body,
                    pub_date = parse_plone_date(
                                            comment['date']).astimezone(UTC),
                    author_email = None,
                    author_url = None,
                    remote_addr = None,
                    parent = comment['parent'],
                    parser = 'text',
                    status = COMMENT_MODERATED
                    )

            # Re-thread comments
            for comment in comments.values():
                comment.parent = comments.get(comment.parent, None)


            posts[entry['id']] = Post(
                slug=gen_timestamped_slug(entry['id'],
                                          'entry', pub_date),
                title=subject,
                link=entry['url'],
                pub_date=pub_date.astimezone(UTC),
                author=authors[entry['author']],
                intro=u'',
                body=body,
                tags=itemtags,
                categories=[],
                comments=comments.values(),
                comments_enabled=entry['allow_comments'],
                pings_enabled=True,
                uid=entry['id'],
                parser=parser,
                content_type='entry',
                status=status
                )
            yield _(u'<li><strong>%s</strong> (by %s; %d comments)</li>') % (
                subject, author.username, len(comments))

        yield _(u'</ol>')
        self.enqueue_dump(Blog(
            title,
            blogurl,
            '',
            'en',
            tags.values(),
            [],
            posts.values(),
            authors.values()))
        flash(_(u'Added imported items to queue.'))

        yield _(u'<p><strong>All done.</strong></p>')

    def configure(self, request):
        form = QuillsImportForm()

        if request.method == 'POST' and form.validate(request.form):
            return self.render_admin_page(
                'admin/import_quills_process.html',
                live_log=self.import_quills(
                      blogurl = form.data['blogurl'],
                      username = form.data['username'],
                      password = form.data['password']),
                _stream=True)

        if have_pygments:
            code_formatter = HtmlFormatter(cssclass='syntax')
            add_header_snippet('<style type="text/css">\n%s\n</style>' %
                               escape(code_formatter.get_style_defs()))
            exportscript = highlight(EXPORTSCRIPT,
                                     get_lexer_by_name('python'),
                                     code_formatter)
        else:
            exportscript = '<pre>%s</pre>' % escape(EXPORTSCRIPT)

        return self.render_admin_page('admin/import_quills.html',
                                      exportscript=exportscript,
                                      form=form.as_widget())


def setup(app, plugin):
    app.add_importer(QuillsImporter)
    app.add_template_searchpath(TEMPLATES)


if __name__ == '__main__':
    import doctest
    doctest.testmod()
