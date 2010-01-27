# -*- coding: utf-8 -*-
"""
    zine.plugins.zaiki_theme
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~

    A theme based on the Fluid 960 CSS framework.

    :copyright: (c) 2009 by Kiran Jonnalagadda
    :license: New BSD license for theme, MIT/GPL for Fluid 960.
"""
import os.path
from urllib import urlencode
try: from hashlib import md5
except ImportError: from md5 import new as md5
from werkzeug import escape
from zine.api import url_for, get_application, _
from zine.widgets import Widget
import zine.i18n
from zine.application import Theme
from zine.utils import forms
from zine.models import Post

TEMPLATE_FILES = os.path.join(os.path.dirname(__file__), 'templates')
SHARED_FILES = os.path.join(os.path.dirname(__file__), 'shared')
THEME_SETTINGS = {
    'date.time_format.default': 'h:mm a'
    }


class SimpleWidget(Widget):
    def __init__(self, show_title=False):
        self.show_title = show_title


class BlurbWidget(Widget):
    name = 'blurb_widget'
    template = 'widgets/blurb_widget.html'

    def __init__(self, show_title=False):
        self.show_title = show_title
        cfg = get_application().cfg
        slug = cfg['zaiki_theme/blurb_more_page']
        if slug:
            self.blurbpage = Post.query.filter_by(slug=slug).first()
        else:
            self.blurbpage = None


class FlickrWidget(SimpleWidget):
    name = 'flickr_widget'
    template = 'widgets/flickr_widget.html'


class TwitterWidget(SimpleWidget):
    name = 'twitter_widget'
    template = 'widgets/twitter_widget.html'


class DopplrWidget(SimpleWidget):
    name = 'dopplr_widget'
    template = 'widgets/dopplr_widget.html'


class DailymileWidget(SimpleWidget):
    name = 'dailymile_widget'
    template = 'widgets/dailymile_widget.html'


class ZaikiTheme(Theme):
    """
    Theme helper for the Zaiki theme.
    """
    def format_time(self, time=None, format=None):
        format = self._get_babel_format('time', format)
        return zine.i18n.format_time(time, format)

    def avatar(self, comment, size=80):
        if comment.user is None:
            email = comment._email
            www = comment._www
        else:
            email = comment.user.email
            www = comment.user.www
        if email:
            #: Return Gravatar URL
            return u"http://www.gravatar.com/avatar.php?" + urlencode(
                {'gravatar_id':md5(email).hexdigest(),
                 'size': size, 'default': 'identicon'})
        elif www and www.find('livejournal.com') != -1:
            #: Return LiveJournal userpic
            return u"http://ljpic.seacrow.com/geturl?" + urlencode(
                {'url': www})
        return url_for('zaiki_theme/shared', filename='img/user.gif')

    def amp(self, text):
        """
        Place & in a <span class="amp" /> tag and return escaped text.
        """
        return escape(text).replace('&amp;', '<span class="amp">&amp;</span>')
    

def setup(app, plugin):
    theme = ZaikiTheme('zaiki', TEMPLATE_FILES, plugin.metadata,
                          THEME_SETTINGS)
    app.add_theme(theme)
    app.add_template_filter('timeformat', theme.format_time)
    app.add_template_filter('avatar', theme.avatar)
    app.add_template_filter('amp', theme.amp)
    app.add_shared_exports('zaiki_theme', SHARED_FILES)
    app.add_config_var('zaiki_theme/blurb', forms.TextField(
                       widget=forms.Textarea))
    app.add_config_var('zaiki_theme/blurb_more_page', forms.TextField(
                       default='about'))
    app.add_config_var('zaiki_theme/copyright', forms.TextField())
    app.add_config_var('zaiki_theme/license', forms.TextField())
    
    # Widgets
    app.add_widget(BlurbWidget)
    app.add_widget(FlickrWidget)
    app.add_widget(TwitterWidget)
    app.add_widget(DopplrWidget)
    app.add_widget(DailymileWidget)

    # Flickr widget
    app.add_config_var('zaiki_theme/flickr_machinetag', forms.TextField())
    app.add_config_var('zaiki_theme/flickr_user', forms.TextField())
    app.add_config_var('zaiki_theme/flickr_api_key', forms.TextField())
    app.add_config_var('zaiki_theme/flickr_api_secret', forms.TextField())
    app.add_config_var('zaiki_theme/flickr_pic_count', forms.IntegerField(
                       default=6))
    app.add_config_var('zaiki_theme/flickr_pic_display', forms.TextField(
                       default='random'))
    app.add_config_var('zaiki_theme/flickr_pic_size', forms.TextField(
                       default='s'))

    # Twitter widget
    app.add_config_var('zaiki_theme/twitter_user', forms.TextField())

    # Dopplr widget
    app.add_config_var('zaiki_theme/dopplr_user', forms.TextField(default=''))
    app.add_config_var('zaiki_theme/dopplr_script_id', forms.TextField(
                       default=''))

    # Dailymile widget
    app.add_config_var('zaiki_theme/dailymile_user', forms.TextField(
                       default=''))
