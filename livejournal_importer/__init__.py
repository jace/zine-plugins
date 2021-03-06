# -*- coding: utf-8 -*-
import os.path
import re
import xmlrpclib
from werkzeug import url_unquote_plus, escape, unescape
try: from hashlib import md5
except ImportError: from md5 import new as md5
from time import strptime, sleep
from datetime import date, datetime, timedelta
from lxml import etree
from pytz import UTC
from zine.api import *
from zine.importers import Importer, Blog, Tag, Category, Author, Post, Comment
from zine.i18n import get_timezone
from zine.utils import forms, log
from zine.utils.validators import ValidationError, check
from zine.utils.admin import flash
from zine.utils.http import redirect_to
from zine.utils.net import urlparse, HTTPHandler
from zine.utils.text import gen_slug, gen_timestamped_slug
from zine.models import COMMENT_MODERATED, COMMENT_BLOCKED_USER, \
     COMMENT_DELETED, STATUS_PUBLISHED, STATUS_PROTECTED, STATUS_PRIVATE
import zine.models

__version__ = '0.2'

TEMPLATES = os.path.join(os.path.dirname(__file__), 'templates')

LIVEJOURNAL_RPC='http://www.livejournal.com/interface/xmlrpc'
LIVEJOURNAL_COMMENTS='http://www.livejournal.com/export_comments.bml'
TIMEOUT=120 # Wait at least two minutes for server to respond

IMPORT_JOURNAL=1
IMPORT_COMMUNITY=2
IMPORT_COMMUNITY_ALL=3

SECURITY_DISCARD=1
SECURITY_PRIVATE=2
SECURITY_PROTECTED=3
SECURITY_PUBLIC=4

ljuser_re = re.compile(r'''<lj\s+(user|comm)\s*=\s*"?'?(\w+)"?'?\s*>''', re.U | re.I)
tag_re = re.compile(r'</?(\w+).*?/?>', re.IGNORECASE | re.UNICODE)


def is_valid_lj_user(message=None):
    """
    Validates LiveJournal username.

    >>> check(is_valid_lj_user, 'jace')
    True
    >>> check(is_valid_lj_user, 'Jace')
    False
    >>> check(is_valid_lj_user, '__hi__')
    True
    >>> check(is_valid_lj_user, 'hi-there')
    False
    >>> check(is_valid_lj_user, '9inch')
    True
    >>> check(is_valid_lj_user, 'jace.livejournal.com')
    False
    >>> check(is_valid_lj_user, '')
    False
    """
    if message is None:
        message = lazy_gettext(u'"%s" is not a valid LiveJournal user name.')
    def validator(form, value):
        if not value or re.search('[^a-z0-9_]', value):
            raise ValidationError(message % value)
    return validator


def url_to_journal(user):
    """
    Return a URL to the specified user's journal. Doesn't recognise communities.

    >>> url_to_journal('jace')
    'http://jace.livejournal.com/'
    >>> url_to_journal('hi_there')
    'http://hi-there.livejournal.com/'
    >>> url_to_journal('__hi__')
    'http://users.livejournal.com/__hi__/'
    """
    is_valid_lj_user()(None, user)
    if user.startswith('_') or user.endswith('_'):
        return 'http://users.livejournal.com/%s/' % user
    else:
        return 'http://%s.livejournal.com/' % user.replace('_', '-')


class LiveJournalConnect:
    """
    XML-RPC gateway to LiveJournal. Performs a challenge-response authentication
    before each request.
    """
    # Surely there's a cleaner way to do this using decorators?
    class LiveJournalConnectMethod:
        def __init__(self, parent, method):
            self._parent = parent
            self._method = method

        def _pause_for(self, seconds):
            if not self._parent._lastcalltime:
                return
            now = datetime.now()
            if now > self._parent._lastcalltime:
                delta = now - self._parent._lastcalltime
                if delta.days > 0:
                    return # We're only meant for a few seconds
                elif delta.seconds >= seconds:
                    return # Waiting period already passed
                elif seconds == 1:
                    return # Sleep can't handle sub-second precision
                else:
                    sleep(seconds - delta.seconds)

        def __call__(self, **kw):
            # Don't call API more than once in two seconds.
            ##self._pause_for(2)
            challenge = self._parent._server.LJ.XMLRPC.getchallenge()[
                                                                    'challenge']
            response = md5(challenge + md5(self._parent._pass).hexdigest()
                                                                ).hexdigest()
            parms = {'username': self._parent._user,
                     'auth_method': 'challenge',
                     'auth_challenge': challenge,
                     'auth_response': response,
                     'ver': 1}
            if self._parent._journal:
                parms['usejournal'] = self._parent._journal
            parms.update(kw)
            for key, value in parms.items():
                if isinstance(value, unicode):
                    parms[key] = value.encode('utf-8')

            result = getattr(self._parent._server.LJ.XMLRPC,
                             self._method)(parms)
            self._parent._lastcalltime = datetime.now()
            return result

    def __init__(self, username, password, usejournal=None):
        self._server = xmlrpclib.Server(LIVEJOURNAL_RPC)
        self._user = username
        self._pass = password
        self._journal = usejournal
        self._lastcalltime = None

    def __getattr__(self, method):
        return self.LiveJournalConnectMethod(self, method)


class LiveJournalImportForm(forms.Form):
    """This form asks the user for authorisation and import options."""
    username = forms.TextField(lazy_gettext(u'LiveJournal username'),
                               required=True,
                               validators=[is_valid_lj_user()])
    password = forms.TextField(lazy_gettext(u'LiveJournal password'),
                               required=True,
                               widget=forms.PasswordInput)
    import_what = forms.ChoiceField(lazy_gettext(u'Import what?'),
        choices=[(IMPORT_JOURNAL, lazy_gettext(u'My Journal')),
              (IMPORT_COMMUNITY, lazy_gettext(u'My Posts in Community')),
              (IMPORT_COMMUNITY_ALL, lazy_gettext(u'Everything in Community'))],
        help_text=lazy_gettext(u'Importing a community requires '\
                               u'administrative access to the community.'),
        default=IMPORT_JOURNAL, required=True,
        widget=forms.RadioButtonGroup)
    community = forms.TextField(lazy_gettext(u'Community name'))
    security_choices = [(SECURITY_DISCARD, lazy_gettext(u'Don’t import')),
                        (SECURITY_PRIVATE, lazy_gettext(u'Private')),
                        (SECURITY_PROTECTED, lazy_gettext(u'Protected')),
                        (SECURITY_PUBLIC, lazy_gettext(u'Public'))]
    security_custom = forms.ChoiceField(lazy_gettext(
            u'Convert custom-security entries to'), choices=security_choices,
            help_text=lazy_gettext(u'Zine only supports public, private and '\
                                   u'protected entries, so you must choose '\
                                   u'what to do with your custom security '\
                                   u'entries.'))
    categories = forms.Multiple(forms.TextField(),
                                lazy_gettext(u'Categories'),
                                help_text=lazy_gettext(u'Choose categories to '\
                                                  u'assign imported posts to.'),
                                widget=forms.CheckboxGroup)
    getcomments = forms.BooleanField(lazy_gettext(u'Download Comments?'))

    def __init__(self, initial=None):
        initial = forms.fill_dict(initial,
            getcomments=True,
            import_what=IMPORT_JOURNAL,
            security_custom=SECURITY_PROTECTED,
        )
        self.categories.choices = [(c.name, c.name) for c in
                                   zine.models.Category.query.all()]
        forms.Form.__init__(self, initial)

    def context_validate(self, data):
        lj = LiveJournalConnect(data['username'], data['password'])
        try:
            result = lj.login()
        except xmlrpclib.Fault, fault:
            raise ValidationError(fault.faultString)
        if data['import_what'] in [IMPORT_COMMUNITY, IMPORT_COMMUNITY_ALL]:
            if data['community'] not in result.get('usejournals', []):
                raise ValidationError(lazy_gettext(u'You do not have access '\
                    u'to the specified community.'))


def parse_lj_date(value):
    try:
        result = datetime(*(strptime(value, '%Y-%m-%d %H:%M:%S')[:6]))
    except ValueError:
        result = datetime(*(strptime(value, '%Y-%m-%d%H:%M:%S')[:6]))
    return result # Don't attach timezone here.


class LiveJournalImporter(Importer):
    name = 'livejournal'
    title = 'LiveJournal'

    def import_livejournal(self, username, password, import_what=IMPORT_JOURNAL,
                           community='', security_custom=SECURITY_PROTECTED,
                           categories=[], getcomments=True):
        """Import from LiveJournal using specified parameters."""
        yield _(u'<p>Beginning LiveJournal import. Attempting to login...</p>')
        if import_what != IMPORT_JOURNAL:
            usejournal = community
        else:
            usejournal = None
        lj = LiveJournalConnect(username, password, usejournal)
        result = lj.login(getmoods=0)
        authors = {username: Author(username=username, email='',
                        real_name=unicode(result['fullname'], 'utf-8'))}
        yield _(u'<p>Your name: <strong>%s</strong></p>') % \
                                                    authors[username].real_name
        moodlist = dict([(int(m['id']), unicode(str(m['name']),
                                      'utf-8')) for m in result['moods']])

        result = lj.getusertags()
        tags = dict([(tag, Tag(gen_slug(tag), tag))
            for tag in [unicode(t['name'], 'utf-8') for t in result['tags']]])
        yield _(u'<p><strong>Tags:</strong> %s</p>')% _(u', ').join(tags.keys())

        ##result = lj.getdaycounts()
        ##daycounts = [(date(*strptime(item['date'], '%Y-%m-%d')[0:3]),
        ##              item['count']) for item in result['daycounts']]
        ##totalposts = sum([x[1] for x in daycounts])
        ##yield _(u'<p>Found <strong>%d</strong> posts on <strong>%d days'\
        ##        u'</strong> between %s and %s.</p>') % (
        ##                                totalposts,
        ##                                len(daycounts),
        ##                                daycounts[0][0].strftime('%Y-%m-%d'),
        ##                                daycounts[-1][0].strftime('%Y-%m-%d'))

        posts = {}

        # Process implemented as per
        # http://www.livejournal.com/doc/server/ljp.csp.entry_downloading.html
        yield _(u'<ul>')
        yield _(u'<li>Getting metadata...</li>')
        result = lj.syncitems()
        sync_items = []
        sync_total = int(result['total'])
        yield _(u'<li>%d items...</li>') % sync_total
        sync_items.extend(result['syncitems'])
        while len(sync_items) < sync_total:
            lastsync = max([parse_lj_date(item['time']) for item in sync_items]
                          ).strftime('%Y-%m-%d %H:%M:%S')
            yield _(u'<li>Got %d items up to %s...</li>') % (len(sync_items), lastsync)
            result = lj.syncitems(lastsync=lastsync)
            sync_items.extend(result['syncitems'])
        yield _(u'<li>Got all %d items.</li>') % len(sync_items)
        yield _(u'</ul>')
        #: Discard non-journal items.
        sync_items = [i for i in sync_items if i['item'].startswith('L-')]
        yield _(u'<p>Downloading <strong>%d</strong> entries...</p>') % len(sync_items)
        # Track what items we need to get
        sync_data = {}
        for item in sync_items:
            sync_data[int(item['item'][2:])] = {
                'downloaded': False,
                'time': parse_lj_date(item['time'])
            }

        # Start downloading bodies
        sync_left = [sync_data[x] for x in sync_data
                                        if sync_data[x]['downloaded'] is False]
        if sync_left:
            lastsync = (min([x['time'] for x in sync_left])-timedelta(seconds=1)
                                                 ).strftime('%Y-%m-%d %H:%M:%S')
        while len(sync_left) > 0:
            yield _(u'<p>Getting a batch...</p>')
            try:
                result = lj.getevents(selecttype='syncitems', lastsync=lastsync)
            except xmlrpclib.Fault, fault:
                if fault.faultCode == 406:
                    # LJ doesn't like us. Go back one second and try again.
                    yield _(u'<p>LiveJournal says we are retrying the same '\
                            u'date and time too often. Trying again with the '\
                            u'time set behind by one second.</p>')
                    lastsync = (parse_lj_date(lastsync) - timedelta(seconds=1)
                                ).strftime('%Y-%m-%d %H:%M:%S')
                    continue
                else:
                    yield _(u'<p>Process failed. LiveJournal says: '\
                            u'(%d) %s</p>') % (fault.faultCode,
                                               fault.faultString)
                    break

            yield _(u'<ol start="%d">') % (len(posts) + 1)
            for item in result['events']:
                if sync_data[item['itemid']]['downloaded'] is True:
                    # Dupe, thanks to our lastsync time manipulation. Skip.
                    continue
                sync_data[item['itemid']]['downloaded'] = True
                sync_data[item['itemid']]['item'] = item

                subject = item.get('subject', '')
                if isinstance(subject, xmlrpclib.Binary):
                    subject = subject.data
                subject = unicode(str(subject), 'utf-8')
                #: LiveJournal subjects may contain HTML tags. Strip them and
                #: convert HTML entities to Unicode equivalents.
                subject = unescape(tag_re.sub('', ljuser_re.sub('\\2', subject)))
                poster = item.get('poster', username)
                if poster != username and import_what != IMPORT_COMMUNITY_ALL:
                    # Discard, since we don't want this.
                    yield _(u'<li><strong>Discarded:</strong> %s <em>(by %s)</em></li>') % (subject,
                                                                       poster)
                    continue
                if poster not in authors:
                    authors[poster] = Author(poster, '', '')
                # Map LiveJournal security codes to Zine status flags
                security = item.get('security', 'public')
                if security == 'usemask' and item['allowmask'] == 1:
                    security = 'friends'
                if security == 'usemask':
                    status = {
                        SECURITY_DISCARD: None,
                        SECURITY_PUBLIC: STATUS_PUBLISHED,
                        SECURITY_PROTECTED: STATUS_PROTECTED,
                        SECURITY_PRIVATE: STATUS_PRIVATE
                    }[security_custom]
                    if status is None:
                        yield _(u'<li><strong>Discarded (masked):</strong> '\
                                u'%s</li>') % subject
                        continue
                else:
                    status = {
                        'public': STATUS_PUBLISHED,
                        'friends': STATUS_PROTECTED,
                        'private': STATUS_PRIVATE,
                        }[security]
                    
                #: Read time as local timezone and then convert to UTC. Zine
                #: doesn't seem to like non-UTC timestamps in imports.
                pub_date = get_timezone().localize(parse_lj_date(
                    item['eventtime'])).astimezone(UTC)
                itemtags = [t.strip() for t in unicode(item['props'].get(
                                            'taglist', ''), 'utf-8').split(',')]
                while '' in itemtags: itemtags.remove('')
                itemtags = [tags[t] for t in itemtags]
                extras = {}
                if 'current_music' in item['props']:
                    if isinstance(item['props']['current_music'],
                                  xmlrpclib.Binary):
                        extras['current_music'] = unicode(item['props']
                                                ['current_music'].data, 'utf-8')
                    else:
                        extras['current_music'] = unicode(str(item['props']
                                                    ['current_music']), 'utf-8')
                if 'current_mood' in item['props']:
                    if isinstance(item['props']['current_mood'],
                                  xmlrpclib.Binary):
                        extras['current_mood'] = unicode(item['props']
                                                ['current_mood'].data, 'utf-8')
                    else:
                        extras['current_mood'] = unicode(str(item['props']
                                                    ['current_mood']), 'utf-8')
                elif 'current_moodid' in item['props']:
                    extras['current_mood'] = moodlist[int(item['props']
                                                            ['current_moodid'])]
                if 'current_coords' in item['props']:
                    if isinstance(item['props']['current_coords'],
                                  xmlrpclib.Binary):
                        extras['current_coords'] = unicode(item['props']
                                            ['current_coords'].data, 'utf-8')
                    else:
                        extras['current_coords'] = unicode(str(item['props']
                                                ['current_coords']), 'utf-8')
                if 'current_location' in item['props']:
                    if isinstance(item['props']['current_location'],
                                  xmlrpclib.Binary):
                        extras['current_location'] = unicode(item['props']
                                            ['current_location'].data, 'utf-8')
                    else:
                        extras['current_location'] = unicode(str(item['props']
                                                ['current_location']), 'utf-8')
                if 'picture_keyword' in item['props']:
                    if isinstance(item['props']['picture_keyword'],
                                  xmlrpclib.Binary):
                        extras['picture_keyword'] = unicode(item['props']
                                            ['picture_keyword'].data, 'utf-8')
                    else:
                        extras['picture_keyword'] = unicode(str(item['props']
                                                ['picture_keyword']), 'utf-8')
                
                extras['lj_post_id'] = item['itemid']
                extras['original_url'] = item['url']
                posts[item['itemid']] = Post(
                    #: Generate slug. If there's no subject, use '-'+itemid.
                    #: Why the prefix? Because if the user wants %year%/%month%/
                    #: for the post url format and we end up creating a slug
                    #: like 2003/12/1059, it will conflict with the archive
                    #: access path format of %Y/%m/%d and the post will become
                    #: inaccessible, since archive paths take higher priority
                    #: to slugs in zine's urls.py.
                    slug=gen_timestamped_slug(gen_slug(subject) or
                                              ('-' + str(item['itemid'])),
                                              'entry', pub_date),
                    title=subject,
                    link=item['url'],
                    pub_date=pub_date,
                    author=authors[poster],
                    intro='',
                    body=isinstance(item['event'], xmlrpclib.Binary) and
                            unicode(item['event'].data, 'utf-8') or
                            url_unquote_plus(str(item['event'])),
                    tags=itemtags,
                    categories=[Category(x) for x in categories],
                    comments=[], # Will be updated later.
                    comments_enabled=not item['props'].get(
                                                       'opt_nocomments', False),
                    pings_enabled=False, # LiveJournal did not support pings
                    uid='livejournal;%s;%d' % (usejournal or username,
                                               item['itemid']),
                    parser=item['props'].get('opt_preformatted', False) and
                                                        'html' or 'livejournal',
                    status=status,
                    extra=extras
                    )
                yield _(u'<li>%s <em>(by %s on %s)</em></li>') % (subject, poster, pub_date.strftime('%Y-%m-%d %H:%M'))
            # Done processing batch.
            yield _(u'</ol>')
            sync_left = [sync_data[x] for x in sync_data
                                        if sync_data[x]['downloaded'] is False]
            if sync_left:
                lastsync = (min([x['time'] for x in sync_left]) -
                            timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')

        # ------------------------------------------------------------------
        if getcomments:
            yield _(u"<p>Importing comments...</p>")

            #: Get session key to use for the HTTP request to retrieve comments.
            ljsession = lj.sessiongenerate(expiration='short',
                                           ipfixed=True)['ljsession']

            #: See http://www.livejournal.com/bots/ and
            #: http://www.livejournal.com/doc/server/ljp.csp.auth.cookies.html
            headers = {
                'X-LJ-Auth': 'cookie', # Needed only for flat interface, but anyway
                'Cookie': 'ljsession=%s' % ljsession,
                'User-Agent': 'LiveJournal-Zine/%s '\
                              '(http://bitbucket.org/jace/zine-plugins; '\
                              '<jace at pobox dot com>; en-IN)' % __version__
                }

            c_usermap = {} # User id to LJ user name
            c_info = {} # id: {'posterid', 'state'}

            c_startid = 0
            c_maxid = None

            while c_maxid is None or c_startid <= c_maxid:
                #: See http://www.livejournal.com/developer/exporting.bml and
                #: http://www.livejournal.com/doc/server/ljp.csp.export_comments.html
                conn = HTTPHandler(urlparse.urlsplit(
                    LIVEJOURNAL_COMMENTS + '?get=comment_meta&startid=%d%s' %
                    (c_startid, usejournal and '&authas=%s'%usejournal or '')),
                    timeout=TIMEOUT, method='GET')
                conn.headers.extend(headers)
                yield _(u'<p>Retrieving comment metadata starting from %d...</p>') % c_startid
                c_metadata = etree.fromstring(conn.open().data)

                if not c_maxid:
                    if c_metadata.find('maxid') is not None:
                        c_maxid = int(c_metadata.find('maxid').text)

                for user in c_metadata.find('usermaps'):
                    c_usermap[int(user.attrib['id'])] = user.attrib['user']

                for comment in c_metadata.find('comments'):
                    c_id = int(comment.attrib['id'])
                    c_userid = int(comment.attrib.get('posterid', '0'))
                    c_username = c_usermap.get(c_userid, u'') # Anonymous == blank
                    if c_userid != 0:
                        c_website = url_to_journal(c_username)
                    else:
                        c_website = u''
                    c_info[c_id] = dict(
                        userid = c_userid,
                        username = c_username,
                        author = authors.get(c_username, None),
                        website = c_website,
                        state = {'D': COMMENT_DELETED,
                                 'S': COMMENT_BLOCKED_USER,
                                 'F': COMMENT_MODERATED, # No Frozen state in Zine
                                 'A': COMMENT_MODERATED}[
                                     comment.attrib.get('state', 'A')])

                if not c_maxid:
                    yield _(u'<p>Something wrong with comment retrieval. '\
                            u'LiveJournal will not tell us how many there are. '\
                            u'Aborting.</p>')
                    break
                c_startid = max(c_info.keys()) + 1

            yield _(u'<p>Got metadata for %d comments. Retrieving bodies...</p>') % len(c_info)

            c_startid = 0 # Start over again for comment bodies
            comments = {} # Holds Comment objects.
            while c_startid <= c_maxid:
                conn = HTTPHandler(urlparse.urlsplit(
                    LIVEJOURNAL_COMMENTS + '?get=comment_body&startid=%d%s' %
                    (c_startid, usejournal and "&authas=%s"%usejournal or '')),
                    timeout=TIMEOUT, method='GET')
                conn.headers.extend(headers)
                yield _(u'<p>Retrieving comment bodies starting from %d...</p>') % c_startid
                yield _(u'<ol>')
                c_bodies = etree.fromstring(conn.open().data)
                for comment in c_bodies.find('comments'):
                    c_id = int(comment.attrib['id'])
                    info = c_info[c_id]
                    bodytag = comment.find('body')
                    subjecttag = comment.find('subject')
                    body = bodytag is not None and bodytag.text or u''
                    if subjecttag is not None:
                        body = u'<span class="subject">%s</span>\n%s'%(
                            subjecttag.text, body)
                    datetag = comment.find('date')
                    if datetag is None: # Deleted comments have no date
                        pub_date = None
                    else:
                        pub_date = UTC.localize(datetime(*(strptime(
                                                    comment.find('date').text,
                                                    '%Y-%m-%dT%H:%M:%SZ')[:6])))
                    remote_addr = None
                    if comment.find('property'):
                        for property in comment.find('property'):
                            if property.attrib['name'] == 'poster_ip':
                                remote_addr = property.text
                    comments[c_id] = Comment(
                        author=info['author'] or info['username'],
                        body = body,
                        author_email = None,
                        author_url = not info['author']
                            and info['website'] or None,
                        parent = 'parentid' in comment.attrib and int(
                            comment.attrib['parentid']) or None,
                        pub_date = pub_date,
                        remote_addr = remote_addr,
                        parser = u'livejournal',
                        status = info['state'],
                    )
                    postid = int(comment.attrib['jitemid'])
                    c_info[c_id]['postid'] = postid
                    if postid in posts:
                        posts[postid].comments.append(comments[c_id])
                    else:
                        # Orphan comment, either because post was dropped or
                        # because it is not downloaded yet (only when testing)
                        yield _(u'<li>Dropping orphan comment %d on missing post %d.</li>'
                                ) % (c_id, postid)
                c_startid = max(comments.keys()) + 1
                yield _(u'</ol>')
            # Calculate timestamps for deleted comments.
            yield _(u'<p>Guessing timestamps for deleted comments...</p>')
            sortedcomments = comments.keys()
            sortedcomments.sort()
            totalcomments = len(sortedcomments)
            for counter in range(totalcomments):
                comment = comments[sortedcomments[counter]]
                if comment.pub_date is None:
                    prev_time = comments[sortedcomments[max(0, counter-1)]].pub_date
                    next_time = comments[sortedcomments[min(totalcomments-1, counter+1)]].pub_date
                    if prev_time is None and next_time is None:
                        # No luck with finding time from neighbouring
                        # comments. Let's look for the post instead.
                        c_id = sortedcomments[counter]
                        postid = c_info['c_id']['postid']
                        if postid in posts:
                            new_time = posts[postid].pub_date
                        # else: orphaned comment, anyway. don't bother.
                    elif next_time is None:
                        new_time = prev_time
                    elif prev_time is None:
                        new_time = next_time
                    else:
                        # Midway between previous and next
                        new_time = prev_time + (next_time - prev_time)/2
                    # Save new timestamp
                    comment.pub_date = new_time
            # Re-thread comments
            yield _(u'<p>Rethreading comments...</p>')
            for comment in comments.values():
                comment.parent = comments.get(comment.parent, None)
        else:
            yield _(u'<p>Skipping comment import.</p>')
        # --------------------------------------------------------------------


        self.enqueue_dump(Blog(
            usejournal or username,
            url_to_journal(username),
            '',
            'en',
            tags.values(),
            [],
            posts.values(),
            authors.values()))
        flash(_(u'Added imported items to queue.'))

        yield _(u'<p><strong>All done.</strong></p>')

    def configure(self, request):
        form = LiveJournalImportForm()

        if request.method == 'POST' and form.validate(request.form):
            return self.render_admin_page(
                'admin/import_livejournal_process.html',
                live_log=self.import_livejournal(
                      username = form.data['username'],
                      password = form.data['password'],
                      import_what = form.data['import_what'],
                      community = form.data['community'],
                      security_custom = form.data['security_custom'],
                      categories = form.data['categories'],
                      getcomments = form.data['getcomments']),
                _stream=True)

        return self.render_admin_page('admin/import_livejournal.html',
                                      form=form.as_widget())


def setup(app, plugin):
    app.add_importer(LiveJournalImporter)
    app.add_template_searchpath(TEMPLATES)


if __name__ == '__main__':
    import doctest
    doctest.testmod()
