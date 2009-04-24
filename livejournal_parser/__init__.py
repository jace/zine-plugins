import os.path
import re
from zine.api import *
from zine.parsers import BaseParser
from zine.utils.zeml import sanitize, parse_html

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


class LiveJournalParser(BaseParser):
    """A LiveJournal markup parser.
    
    >>> l = LiveJournalParser(app=None)
    >>> l.parse(u'This is some\\ntext here.', 'comment').to_html()
    u'This is some<br>text here.'

    >>> l.parse(u'More\\r\\ntext.', 'comment').to_html()
    u'More<br>text.'

    >>> l.parse(u'This is <b>boldly</b> allowed.', 'comment').to_html()
    u'This is <b>boldly</b> allowed.'

    >>> l.parse(u'Type here: <input type="text" name="password" />', 'entry').to_html()
    u'Type here: <input type="text" name="password">'

    >>> l.parse(u'Type here: <input type="text" name="password" />', 'comment').to_html()
    u'Type here: '

    >>> l.parse('Say hi to <lj user="jace">.', 'comment').to_html()
    u'Say hi to <span class="livejournal"><a href="http://jace.livejournal.com/profile"><img width="17" alt="[info]" src="http://l-stat.livejournal.com/img/userinfo.gif" height="17"></a><a href="http://jace.livejournal.com/">jace</a></span>.'

    >>> l.parse('<lj user=jace>!', 'comment').to_html()
    u'<span class="livejournal"><a href="http://jace.livejournal.com/profile"><img width="17" alt="[info]" src="http://l-stat.livejournal.com/img/userinfo.gif" height="17"></a><a href="http://jace.livejournal.com/">jace</a></span>!'

    >>> l.parse('<lj user="hi_there">!', 'comment').to_html()
    u'<span class="livejournal"><a href="http://hi-there.livejournal.com/profile"><img width="17" alt="[info]" src="http://l-stat.livejournal.com/img/userinfo.gif" height="17"></a><a href="http://hi-there.livejournal.com/">hi_there</a></span>!'

    >>> l.parse('<lj user="_oh_my_">', 'comment').to_html()
    u'<span class="livejournal"><a href="http://users.livejournal.com/_oh_my_/profile"><img width="17" alt="[info]" src="http://l-stat.livejournal.com/img/userinfo.gif" height="17"></a><a href="http://users.livejournal.com/_oh_my_/">_oh_my_</a></span>'

    >>> l.parse('<lj comm="bangalore">', 'comment').to_html()
    u'<span class="livejournal"><a href="http://community.livejournal.com/bangalore/profile"><img width="16" alt="[info]" src="http://l-stat.livejournal.com/img/community.gif" height="16"></a><a href="http://community.livejournal.com/bangalore/">bangalore</a></span>'
    """

    name = _(u'LiveJournal')
    settings = dict(file_insertion_enabled=0,
                    raw_enabled=0,
                    output_encoding='unicode',
                    input_encoding='unicode',
                    initial_header_level=4)

    def parse(self, input_data, reason):
        # Steps:
        # 1. Convert <lj user> and <lj comm> tags.
        # 2. Strip all but allowed tags.
        # 3. Strip tag dangerous attributes.
        # 3. Convert newlines to br tags.
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
                       u'src="http://l-stat.livejournal.com/img/userinfo.gif" '\
                       u'alt="[info]" width="17" height="17" /></a><a '\
                       u'href="%s">%s</a></span>' % (profileurl, url, userid)
            else:
                return u'<span class="livejournal"><a href="%s"><img '\
                      u'src="http://l-stat.livejournal.com/img/community.gif" '\
                      u'alt="[info]" width="16" height="16" /></a><a '\
                      u'href="%s">%s</a></span>' % (profileurl, url, userid)

        def _checktag(matchobj):
            tag = matchobj.group(1)
            if tag in allowed_tags.get(reason in allowed_tags and reason
                                                                    or 'post'):
                return matchobj.group(0)
            else:
                return u''

        return sanitize(parse_html('<p>%s</p>' % tag_re.sub(_checktag,
                    ljuser_re.sub(_makeuserlink, input_data)).replace('\r\n',
                        '\n').replace('\n\n', '</p><p>').replace('\n', '<br>')))


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
