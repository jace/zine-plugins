import os.path
import re
import xmlrpclib
from urllib import unquote_plus
try: from hashlib import md5
except ImportError: from md5 import new as md5
from time import strptime, sleep
from datetime import date, datetime, timedelta
from zine.api import *
from zine.importers import Importer, Blog, Tag, Category, Author, Post, Comment
from zine.i18n import get_timezone
from zine.utils import forms, log
from zine.utils.net import open_url
from zine.utils.validators import ValidationError, check
from zine.utils.admin import flash
from zine.utils.http import redirect_to
from zine.utils.text import gen_slug, gen_timestamped_slug
from zine.models import COMMENT_MODERATED, COMMENT_BLOCKED_USER, \
     COMMENT_DELETED, STATUS_PUBLISHED

TEMPLATES = os.path.join(os.path.dirname(__file__), 'templates')

LIVEJOURNAL_RPC='http://www.livejournal.com/interface/xmlrpc'
LIVEJOURNAL_COMMENTS='http://www.livejournal.com/export_comments.bml'

IMPORT_JOURNAL=1
IMPORT_COMMUNITY=2
IMPORT_COMMUNITY_ALL=3

SECURITY_DISCARD=1
SECURITY_PUBLIC=2


def is_valid_lj_user(message=None):
    """
    Validates LiveJournal username.
    
    >>> check(is_valid_lj_user, 'jace')
    True
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
        message = lazy_gettext(u'That is not a valid LiveJournal user name.')
    def validator(form, value):
        if not value or re.search('[^a-z0-9_]', value):
            raise ValidationError(message)
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
    security_choices = [(SECURITY_DISCARD, lazy_gettext(u'Discard')),
                        (SECURITY_PUBLIC, lazy_gettext(u'Make Public'))]
    security_friends = forms.ChoiceField(lazy_gettext(
        u'Convert friends-only entries to'), choices=security_choices,
        help_text=lazy_gettext(u'Zine only supports public entries, so you '\
                               u'must choose what to do with your protected '\
                               u'entries.'))
    security_custom = forms.ChoiceField(lazy_gettext(
            u'Convert custom-security entries to'), choices=security_choices)
    security_private = forms.ChoiceField(lazy_gettext(
            u'Convert private entries to'), choices=security_choices)

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
    return result.replace(tzinfo=get_timezone())
    

class LiveJournalImporter(Importer):
    name = 'livejournal'
    title = 'LiveJournal'

    def import_livejournal(self, username, password, import_what=IMPORT_JOURNAL,
                           community='', security_friends=SECURITY_DISCARD,
                           security_custom=SECURITY_DISCARD,
                           security_private=SECURITY_DISCARD):
        """Import from LiveJournal using specified parameters."""
        blog = None
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
        lastsync = '1900-01-01 00:00:00'
        yield _(u'<p>Getting metadata... ')
        result = lj.syncitems()
        sync_items = []
        sync_total = int(result['total'])
        yield _(u'%d items... ') % sync_total
        sync_items.extend(result['syncitems'])
        while len(sync_items) < sync_total:
            lastsync = max([parse_lj_date(item['time']) for item in sync_items]
                          ).strftime('%Y-%m-%d %H:%M:%S')
            yield _(u'got %d items up to %s... ') % (len(sync_items), lastsync)
            result = lj.syncitems(lastsync=lastsync)
            sync_items.extend(result['syncitems'])
        yield _(u'got all %d items.</p>') % len(sync_items)
        # Discard non-journal items.
        sync_items = [i for i in sync_items if i['item'].startswith('L-')]
        yield _(u'<p>Downloading <strong>%d</strong> entries.</p>') % sync_total
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

                poster = item.get('poster', username)
                if poster != username and import_what != IMPORT_COMMUNITY_ALL:
                    # Discard, since we don't want this.
                    yield _(u'Discarded: %s (by %s)') % (item['subject'],
                                                         poster)
                    continue
                if poster not in authors:
                    authors[poster] = Author(poster, '', '')
                subject = item.get('subject', '')
                if isinstance(subject, xmlrpclib.Binary):
                    subject = subject.data
                subject = str(subject)
                subject = unicode(subject, 'utf-8')
                security = item.get('security', 'public')
                if security == 'usemask' and item['allowmask'] == 1:
                    security = 'friends'
                if security == 'private' and security_private == \
                                                        SECURITY_DISCARD:
                    yield _(u'<li><strong>Discarded (private):</strong> '\
                            u'%s</li>') % subject
                    continue
                if security == 'friends' and security_friends == \
                                                        SECURITY_DISCARD:
                    yield _(u'<li><strong>Discarded (friends):</strong> '\
                            u'%s</li>') % subject
                    continue
                if security == 'usemask' and security_custom == \
                                                        SECURITY_DISCARD:
                    yield _(u'<li><strong>Discarded (masked):</strong> '\
                            u'%s</li>') % subject
                    continue
                # Import as public post
                pub_date = parse_lj_date(item['eventtime'])
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
                extras['lj_post_id'] = item['itemid']
                extras['lj_post_url'] = item['url']
                posts[item['itemid']] = Post(
                    slug=gen_timestamped_slug(subject or item['itemid'],
                                              'entry', pub_date),
                    title=subject,
                    link=item['url'],
                    pub_date=pub_date,
                    author=authors[poster],
                    intro='',
                    body=isinstance(item['event'], xmlrpclib.Binary) and
                            unicode(item['event'].data, 'utf-8') or
                            unicode(unquote_plus(str(item['event'])), 'utf-8'),
                    tags=itemtags,
                    categories=None,
                    comments=None, # Will be updated later.
                    comments_enabled=not item['props'].get(
                                                       'opt_nocomments', False),
                    pings_enabled=False, # LiveJournal did not support pings
                    uid=item['itemid'],
                    parser=item['props'].get('opt_preformatted', False) and
                                                        'html' or 'livejournal',
                    extra=extras
                    )
                yield _(u'<li>%s (by %s)</li>') % (subject, poster)
            # Done processing batch.
            yield _(u'</ol>')
            sync_left = [sync_data[x] for x in sync_data
                                        if sync_data[x]['downloaded'] is False]
            if sync_left:
                lastsync = (min([x['time'] for x in sync_left]) -
                            timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')


        yield _(u"<p>Comment import is not supported yet. Saving blog.</p>")

        self.enqueue_dump(Blog(
            authors[username].real_name,
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
                      security_friends = form.data['security_friends'],
                      security_custom = form.data['security_custom'],
                      security_private = form.data['security_private']),
                _stream=True)

        return self.render_admin_page('admin/import_livejournal.html',
                                      form=form.as_widget())
        


def setup(app, plugin):
    app.add_importer(LiveJournalImporter)
    app.add_template_searchpath(TEMPLATES)
    

if __name__ == '__main__':
    import doctest
    doctest.testmod()
