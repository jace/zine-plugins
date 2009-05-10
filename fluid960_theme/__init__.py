# -*- coding: utf-8 -*-
"""
    zine.plugins.fluid960_theme
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~

    A theme based on the Fluid 960 CSS framework.

    :copyright: (c) 2009 by Kiran Jonnalagadda
    :license: New BSD license for theme, MIT/GPL for Fluid 960.
"""
import os.path
from urllib import urlencode
try: from hashlib import md5
except ImportError: from md5 import new as md5
from zine.api import _
import zine.i18n
from zine.application import Theme
from zine.utils import forms

TEMPLATE_FILES = os.path.join(os.path.dirname(__file__), 'templates')
SHARED_FILES = os.path.join(os.path.dirname(__file__), 'shared')
THEME_SETTINGS = {
    'date.time_format.default': 'h:mm a'
    }

gray_variation = u'fluid960_theme::gray.css'
variations = {
    gray_variation:               _('Gray'),
    u'fluid960_theme::blue.css':  _('Blue'),
}

class Fluid960Theme(Theme):
    """
    Theme helper for the fluid960 theme.
    """
    def format_time(self, time=None, format=None):
        format = self._get_babel_format('time', format)
        return zine.i18n.format_time(time, format)

    def avatar(self, comment):
        if comment.user is None:
            if comment._email:
                #: Return Gravatar URL
                return u"http://www.gravatar.com/avatar.php?" + urlencode(
                    {'gravatar_id':md5(comment._email).hexdigest(),
                     'size': 100})
            elif comment._www and comment._www.find('livejournal.com') != -1:
                #: Return LiveJournal userpic
                return u"http://ljpic.seacrow.com/geturl?" + urlencode(
                    {'url': comment._www})
        return u'' # FIXME: Return URL to shared resource with blank image

def setup(app, plugin):
    theme = Fluid960Theme('fluid960', TEMPLATE_FILES, plugin.metadata,
                          THEME_SETTINGS)
    app.add_theme(theme)
    app.add_template_filter('timeformat', theme.format_time)
    app.add_template_filter('avatar', theme.avatar)
    app.add_shared_exports('fluid960_theme', SHARED_FILES)
    app.add_config_var('fluid960_theme/variation',
                       forms.TextField(default=gray_variation))
