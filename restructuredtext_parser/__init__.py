import os.path
from docutils.core import publish_parts
from zine.i18n import _
from zine.api import get_application, url_for
from zine.views.admin import flash, render_admin_response
from zine.privileges import BLOG_ADMIN, require_privilege
from zine.parsers import BaseParser
from zine.utils import forms
from zine.utils.zeml import parse_html
from zine.utils.validators import ValidationError, check

try:
    import use_pygments_for_docutils
except ImportError: # No Pygments
    pass

TEMPLATES = os.path.join(os.path.dirname(__file__), 'templates')
CFG_HEADER_LEVEL = 'restructuredtext_parser/initial_header_level'

def is_valid_header_level(message=None):
    """Ensure level is between 1 and 6, inclusive."""
    if message is None:
        message = _('Header level must be between 1 and 6.')
    def validate(form, level):
        if not isinstance(level, int) or level < 1 or level > 6:
            raise ValidationError(message)


class ConfigurationForm(forms.Form):
    """reStructuredText configuration form."""
    initial_header_level = forms.IntegerField(_(u'Initial Header Level'),
                                        validators=[is_valid_header_level()])


@require_privilege(BLOG_ADMIN)
def show_restructuredtext_config(req):
    """Show reStructuredText Parser configuration options."""
    form = ConfigurationForm(initial=dict(
            initial_header_level=req.app.cfg[CFG_HEADER_LEVEL]))

    if req.method == 'POST' and form.validate(req.form):
        if form.has_changed:
            req.app.cfg.change_single(CFG_HEADER_LEVEL,
                                                form['initial_header_level'])
            flash(_('reStructuredText Parser settings saved.'), 'ok')
    return render_admin_response('admin/restructuredtext_options.html',
                                 'options.restructuredtext',
                                 form=form.as_widget())


def add_config_link(req, navigation_bar):
    """Add a link to the reStructuredText options page"""
    if req.user.has_privilege(BLOG_ADMIN):
        for link_id, url, title, children in navigation_bar:
            if link_id == 'options':
                children.insert(2, ('restructuredtext',
                                    url_for('restructuredtext_parser/config'),
                                    _('reStructuredText')))


class ReStructuredTextParser(BaseParser):
    """A reStructuredText parser."""

    name = _(u'reStructuredText')

    settings = dict(file_insertion_enabled=1,
                    raw_enabled=0,
                    output_encoding='unicode',
                    input_encoding='unicode',
                    doctile_xform=0)

    def parse(self, input_data, reason):
        usesettings = dict(self.settings)

        if reason == 'comment':
            usesettings['file_insertion_enabled'] = 0
        usesettings['initial_header_level'] = get_application().cfg[
                                                            CFG_HEADER_LEVEL]

        parts = publish_parts(input_data, writer_name='html',
                              settings_overrides=usesettings)

        return parse_html(parts['html_body'])


def setup(app, plugin):
    app.add_config_var(CFG_HEADER_LEVEL,
                       forms.IntegerField(default=3))
    app.connect_event('modify-admin-navigation-bar', add_config_link)
    app.add_parser('restructuredtext', ReStructuredTextParser)
    app.add_url_rule('/options/restructuredtext', prefix='admin',
                     endpoint='restructuredtext_parser/config',
                     view=show_restructuredtext_config)
    app.add_template_searchpath(TEMPLATES)
