import os.path
import re
from zine.api import *
from zine.utils import log
from zine.parsers import BaseParser
from zine.utils.zeml import sanitize, parse_zeml, Element

SHARED_FILES = os.path.join(os.path.dirname(__file__), 'shared')

#: List from http://www.livejournal.com/support/faqbrowse.bml?faqid=72
tags = ('a', 'b', 'big', 'blockquote', 'br', 'center', 'cite', 'code',
        'dd', 'div', 'dl', 'dt', 'em', 'font', 'form', 'h1', 'h2', 'h3',
        'h4', 'h5', 'h6', 'hr', 'i', 'img', 'input', 'li', 'marquee',
        'nobr', 'ol', 'option', 'p', 'pre', 'q', 's', 'select', 'small',
        'span', 'strike', 'strong', 'sub', 'sup', 'table', 'td', 'th',
        'tr', 'tt', 'u', 'ul', 'xmp')

allowed_tags = {
    'post': set(tags),
    'comment': set([tag for tag in tags if tag not in (
                                                    'form', 'input', 'select')])
    }

ljuser_re = re.compile(r'''<lj\s+(user|comm)\s*=\s*"?'?(\w+)"?'?\s*>''', re.U)
tag_re = re.compile(r'</?(\w+).*?/?>', re.IGNORECASE | re.UNICODE)
ljcut_re = re.compile(r'</?lj-cut.*?>', re.IGNORECASE | re.UNICODE)
url_re = re.compile(r'''(^|>)([^<]*)(https?|ftp|irc|mailto):(.*?)(:?;?,?\.?[\s\'\"\(\)\[\]<>])''', re.U)

#: http://www.livejournal.com/support/faqbrowse.bml?faqid=26
ljrawtag_re = re.compile(r'<lj-raw>', re.IGNORECASE | re.UNICODE)
ljraw1_re = re.compile(r'(^|</lj-raw>)(.*?)<lj-raw>',
                       re.IGNORECASE | re.UNICODE | re.DOTALL)
ljraw2_re = re.compile(r'</lj-raw>(.*?)(<lj-raw>|$)',
                       re.IGNORECASE | re.UNICODE | re.DOTALL)
ljraw3_re = re.compile(r'(.*)(.*), re.UNICODE | re.DOTALL')

def split_intro(text, reason='entry'):
    """
    Split text at first lj-cut tag.

    >>> split_intro(u'Nothing to see here.')
    (u'', u'Nothing to see here.')
    >>> split_intro(u'This is <lj-cut>cut.')
    (u'This is ', u'cut.')
    >>> split_intro(u'This cut <lj-cut>ends</lj-cut> here.')
    (u'This cut ', u'ends here.')
    >>> split_intro(u'Texted <lj-cut text="more">cut here.')
    (u'Texted ', u'cut here.')
    >>> split_intro(u'Multiple <lj-cut>cuts</lj-cut> on <lj-cut>this line.')
    (u'Multiple ', u'cuts on this line.')
    >>> split_intro(u'No cuts<lj-cut> for comments.', 'comment')
    (u'', u'No cuts for comments.')
    >>> split_intro(u'Cut for <lj-cut>linksearch reason.', 'linksearch')
    (u'Cut for ', u'linksearch reason.')
    """
    if reason == 'comment':
        return (u'', ljcut_re.sub(u'', text))
    elif not ljcut_re.search(text):
        return (u'', text)
    else:
        return tuple([ljcut_re.sub(u'', t) for t in ljcut_re.split(text, maxsplit=1)])


def htmlize_markup(input_data, reason):
    """
    Convert LiveJournal markup to HTML. Returns tuple of intro and body.

    >>> htmlize_markup(u'This is some\\ntext here.', 'comment')
    ('', u'This is some<br>text here.')

    >>> htmlize_markup(u'More\\r\\ntext.', 'comment')
    ('', u'More<br>text.')

    >>> htmlize_markup(u'Double\\n\\nbreak.', 'comment')
    ('', u'Double<br><br>break.')

    >>> htmlize_markup(u'This is <b>boldly</b> allowed.', 'comment')
    ('', u'This is <b>boldly</b> allowed.')

    >>> htmlize_markup(u'Type here: <input type="text" name="password">', 'entry')
    ('', u'Type here: <input type="text" name="password">')

    >>> htmlize_markup(u'Type here: <input type="text" name="password">', 'comment')
    ('', u'Type here: ')

    >>> htmlize_markup(u'Welcome <lj-cut>home.', 'entry')
    (u'Welcome ', u'home.')

    >>> htmlize_markup(u'Welcome <lj-cut>home.', 'comment')
    ('', u'Welcome home.')

    >>> htmlize_markup(u'Cut\\nme <lj-raw>cut\\nme\\nnot</lj-raw>', 'comment')
    ('', u'Cut<br>me cut\\nme\\nnot')

    >>> htmlize_markup(u'Cut\\nme <lj-raw>cut\\nme\\nnot</lj-raw>\\ncut', 'comment')
    ('', u'Cut<br>me cut\\nme\\nnot<br>cut')

    >>> htmlize_markup('Say hi to <lj user="jace">.', 'comment')
    ('', u'Say hi to <span class="livejournal"><a href="http://jace.livejournal.com/profile"><img width="17" alt="[info]" src="http://l-stat.livejournal.com/img/userinfo.gif" height="17"></a><a href="http://jace.livejournal.com/">jace</a></span>.')

    >>> htmlize_markup('<lj user=jace>!', 'comment')
    ('', u'<span class="livejournal"><a href="http://jace.livejournal.com/profile"><img width="17" alt="[info]" src="http://l-stat.livejournal.com/img/userinfo.gif" height="17"></a><a href="http://jace.livejournal.com/">jace</a></span>!')

    >>> htmlize_markup('<lj user="hi_there">!', 'comment')
    ('', u'<span class="livejournal"><a href="http://hi-there.livejournal.com/profile"><img width="17" alt="[info]" src="http://l-stat.livejournal.com/img/userinfo.gif" height="17"></a><a href="http://hi-there.livejournal.com/">hi_there</a></span>!')

    >>> htmlize_markup('<lj user="_oh_my_">', 'comment')
    ('', u'<span class="livejournal"><a href="http://users.livejournal.com/_oh_my_/profile"><img width="17" alt="[info]" src="http://l-stat.livejournal.com/img/userinfo.gif" height="17"></a><a href="http://users.livejournal.com/_oh_my_/">_oh_my_</a></span>')

    >>> htmlize_markup('<lj comm="bangalore">', 'comment')
    ('', u'<span class="livejournal"><a href="http://community.livejournal.com/bangalore/profile"><img width="16" alt="[info]" src="http://l-stat.livejournal.com/img/community.gif" height="16"></a><a href="http://community.livejournal.com/bangalore/">bangalore</a></span>')
    """
    def _makeuserlink(matchobj):
        ljtype = matchobj.group(1)
        userid = matchobj.group(2)
        if ljtype == 'comm':
            url = 'http://community.livejournal.com/%s/' % userid
        elif userid.startswith('_') or userid.endswith('_'):
            url = 'http://users.livejournal.com/%s/' % userid
        else:
            url = 'http://%s.livejournal.com/' % userid.replace('_', '-')
        profileurl = url + 'profile'
        if ljtype == 'user':
            return u'<span class="livejournal"><a href="%s"><img '\
                   u'width="17" alt="[info]" '\
                   u'src="http://l-stat.livejournal.com/img/userinfo.gif" '\
                   u'height="17"></a><a '\
                   u'href="%s">%s</a></span>' % (profileurl, url, userid)
        else:
            return u'<span class="livejournal"><a href="%s"><img '\
                  u'width="16" alt="[info]" '\
                  u'src="http://l-stat.livejournal.com/img/community.gif" '\
                  u'height="16"></a><a '\
                  u'href="%s">%s</a></span>' % (profileurl, url, userid)


    def _checktag(matchobj):
        tag = matchobj.group(1)
        if tag in allowed_tags.get(reason in allowed_tags and reason
                                                                or 'post'):
            return matchobj.group(0)
        else:
            return u''


    def _makelinks(matchobj):
        return '%s%s<a href="%s:%s">%s:%s</a>%s' % (matchobj.group(1),
                                                matchobj.group(2),
                                                matchobj.group(3),
                                                matchobj.group(4),
                                                matchobj.group(3),
                                                matchobj.group(4),
                                                matchobj.group(5))


    def _convertnewlines(matchobj):
        return url_re.sub(_makelinks,
                matchobj.group(1).replace('\r\n', '\n').replace('\n',
                    '<br>') + matchobj.group(2).replace('\r\n',
                        '\n').replace('\n', '<br>'))

    intro, body = split_intro(input_data, reason)
    if ljrawtag_re.search(intro):
        intro = ljraw2_re.sub(_convertnewlines,
                ljraw1_re.sub(_convertnewlines, intro))
    else:
        intro = re.sub(r'(?s)(.*)()', _convertnewlines, intro)
    if ljrawtag_re.search(body):
        body = ljraw2_re.sub(_convertnewlines,
                ljraw1_re.sub(_convertnewlines, body))
    else:
        body = re.sub(r'(?s)(.*)()', _convertnewlines, body)

    intro = tag_re.sub(_checktag,
        ljuser_re.sub(_makeuserlink, intro))
    body = tag_re.sub(_checktag,
        ljuser_re.sub(_makeuserlink, body))
    return (intro, body)


class LiveJournalParser(BaseParser):
    """A LiveJournal markup parser.

    >>> l = LiveJournalParser(app=None)
    >>> l.parse(u'Hello\\n<lj-cut>there!', 'entry').to_html()
    u'<intro>Hello<br></intro>there!'

    >>> l.parse(u'''<strong>Step 1: Preparation</strong>
    ... <a href=".."><img src="test.jpg"></a>
    ... <lj-cut text="Step 2: Demonstration!"><strong>Step 2: Demonstration!</strong>
    ... ''', 'entry').to_html()
    ...
    u'<intro><strong>Step 1: Preparation</strong><br><a href=".."><img src="test.jpg"></a><br></intro><strong>Step 2: Demonstration!</strong><br>'
    """

    name = _(u'LiveJournal')
    settings = dict(file_insertion_enabled=0,
                    raw_enabled=0,
                    output_encoding='unicode',
                    input_encoding='unicode',
                    initial_header_level=4)

    def parse(self, input_data, reason):
        intro_t, body_t = htmlize_markup(input_data, reason)
        intro = sanitize(parse_zeml(intro_t))
        body = sanitize(parse_zeml(body_t))
        # The following complicated procedure is required only because
        # Zine provides no way to cast RootElement objects (which intro
        # and body are) into Element objects (which intro needs to become)
        newintro = Element('intro')
        newintro.children.extend(intro.children)
        newintro.text = intro.text
        for child in newintro.children:
            child.parent = newintro
        body.children.insert(0, newintro)
        newintro.parent = body
        newintro.tail = body.text
        body.text = u''
        return body


def inject_style(req):
    """Add a link for the livejournal stylesheet to each page."""
    add_link('stylesheet', url_for('livejournal_parser/shared',
                                   filename='lj.css'), 'text/css')


def setup(app, plugin):
    app.add_parser('livejournal', LiveJournalParser)
    app.connect_event('after-request-setup', inject_style)
    app.add_shared_exports('livejournal_parser', SHARED_FILES)


if __name__ == '__main__':
    import doctest
    doctest.testmod()
