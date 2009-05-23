import os.path
from werkzeug import escape
from zine.api import *
from zine.views.admin import flash, render_admin_response
from zine.privileges import BLOG_ADMIN, require_privilege
from zine.utils import forms

TEMPLATES = os.path.join(os.path.dirname(__file__), 'templates')

CFG_GEO_POSITION = 'geo_position'
CFG_GEO_REGION = 'geo_region'
CFG_GOOGLE_ANALYTICS_ID = 'google_analytics_id'
CFG_GOOGLE_SITEMAPS_VERIFY = 'google_sitemaps_verify'

def inject_headers(req):
    """Add headers to each page."""
    cfg = req.app.cfg

    # Insert page coordinates
    # TODO: Check if someone else has already inserted geo coordinates for
    # a specific page, and avoid overriding if so. For this, we need to import
    # local from zine.utils and iterate through local.page_metadata looking for
    # item[0] == 'meta' and item[1]['name'] in ['ICBM, 'geo.location']. We're
    # not doing it for now because it doesn't seem very efficient.
    if cfg[CFG_GEO_POSITION]:
        add_meta(name='ICBM', content=','.join(cfg[CFG_GEO_POSITION]))
        add_meta(name='geo.position', content=';'.join(cfg[CFG_GEO_POSITION]))
    if cfg[CFG_GEO_REGION]:
        add_meta(name='geo.region', content=cfg[CFG_GEO_REGION])


    # Insert Google sitemaps verification header
    if cfg[CFG_GOOGLE_SITEMAPS_VERIFY]:
        add_meta(name='verify-v1', content=cfg[CFG_GOOGLE_SITEMAPS_VERIFY])

    # Insert Google Analytics snippet
    if cfg[CFG_GOOGLE_ANALYTICS_ID] and not (req.user and req.user.is_manager):
        add_header_snippet("""
        <script type="text/javascript">
        var gaJsHost = (("https:" == document.location.protocol) ? "https://ssl." : "http://www.");
        document.write(unescape("%%3Cscript src='" + gaJsHost + "google-analytics.com/ga.js' type='text/javascript'%%3E%%3C/script%%3E"));
        </script>
        <script type="text/javascript">
        try {
        var pageTracker = _gat._getTracker("%s");
        pageTracker._trackPageview();
        } catch(err) {}</script>""" % escape(cfg[CFG_GOOGLE_ANALYTICS_ID]))


class ConfigurationForm(forms.Form):
    """Page headers configuration form."""
    geo_position = forms.CommaSeparated(forms.TextField(),
                                                _(u'Geo Location (lat, lon)'))
    geo_region = forms.TextField(_(u'Geo Region (optional)'))
    google_analytics_id = forms.TextField(
                                        _(u'Google Analytics Web Property Id'))
    google_sitemaps_verify = forms.TextField(_(u'Google Sitemaps Verify Code'))

@require_privilege(BLOG_ADMIN)
def show_pageheaders_config(req):
    """Show page header configuration options."""
    cfg = req.app.cfg
    form = ConfigurationForm(initial=dict(
            geo_position = cfg[CFG_GEO_POSITION],
            geo_region = cfg[CFG_GEO_REGION],
            google_analytics_id = cfg[CFG_GOOGLE_ANALYTICS_ID],
            google_sitemaps_verify = cfg[CFG_GOOGLE_SITEMAPS_VERIFY],
            ))

    if req.method == 'POST' and form.validate(req.form):
        if form.has_changed:
            cfg = req.app.cfg.edit()
            cfg[CFG_GEO_POSITION] = form['geo_position']
            cfg[CFG_GEO_REGION] = form['geo_region']
            cfg[CFG_GOOGLE_ANALYTICS_ID] = form['google_analytics_id']
            cfg[CFG_GOOGLE_SITEMAPS_VERIFY] = form['google_sitemaps_verify']
            cfg.commit()
            flash(_('Page header settings saved.'), 'ok')
    return render_admin_response('admin/options.html',
                                 'options.pageheaders', # See add_config_link
                                 form=form.as_widget())


def add_config_link(req, navigation_bar):
    """Add a link to the options page"""
    if req.user.has_privilege(BLOG_ADMIN):
        for link_id, url, title, children in navigation_bar:
            if link_id == 'options':
                children.insert(2, ('pageheaders', # Becomes options.pageheaders
                                    url_for('page_headers/config'),
                                    _('Page Headers')))


def setup(app, plugin):
    app.connect_event('after-request-setup', inject_headers)
    app.connect_event('modify-admin-navigation-bar', add_config_link)
    app.add_config_var(CFG_GEO_REGION, forms.TextField(default=u''))
    app.add_config_var(CFG_GEO_POSITION, forms.CommaSeparated(
                                                forms.TextField(), default=[]))
    app.add_config_var(CFG_GOOGLE_ANALYTICS_ID, forms.TextField(default=u''))
    app.add_config_var(CFG_GOOGLE_SITEMAPS_VERIFY, forms.TextField(default=u''))
    app.add_url_rule('/options/pageheaders', prefix='admin',
                     endpoint='page_headers/config',
                     view=show_pageheaders_config)
    app.add_template_searchpath(TEMPLATES)
    