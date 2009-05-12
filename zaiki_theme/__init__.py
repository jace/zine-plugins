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
from zine.api import url_for, _
import zine.i18n
from zine.application import Theme
from zine.utils import forms

TEMPLATE_FILES = os.path.join(os.path.dirname(__file__), 'templates')
SHARED_FILES = os.path.join(os.path.dirname(__file__), 'shared')
THEME_SETTINGS = {
    'date.time_format.default': 'h:mm a'
    }

class ZaikiTheme(Theme):
    """
    Theme helper for the Zaiki theme.
    """
    def format_time(self, time=None, format=None):
        format = self._get_babel_format('time', format)
        return zine.i18n.format_time(time, format)

    def avatar(self, comment):
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
                 'size': 80, 'default': 'identicon'})
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
    #app.add_config_var('zaiki_theme/variation',
    #                   forms.TextField(default=gray_variation))
