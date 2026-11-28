"""Sphinx docs configuration file."""

# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

import datetime as dt
import pathlib
import tomllib

year = dt.date.today().year

# -- Project information -----------------------------------------------------

project = 'wiki'
author = 'Plasma AI'
copyright = f'{year}: {author}'

# The full version, including alpha/beta/rc tags
cargo = tomllib.loads(pathlib.Path('../Cargo.toml').read_text(encoding='utf-8'))
release = cargo['workspace']['package']['version']

# -- Configure extensions ----------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom ones.
extensions = []

# -- General configuration ---------------------------------------------------

# Add any paths that contain templates here, relative to this directory.
templates_path = ['_templates']

# The suffix of source filenames.
source_suffix = '.rst'

# Changed from `master_doc` in Sphinx 4
root_doc = 'index'

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = []

# -- Options for HTML output -------------------------------------------------

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ['_static']

# Refer to the following links for pydata-sphinx-theme options:
# Configuration (https://pydata-sphinx-theme.readthedocs.io/en/latest/user_guide/configuring.html)
# Customization (https://pydata-sphinx-theme.readthedocs.io/en/latest/user_guide/customizing.html)
html_theme = 'pydata_sphinx_theme'

html_favicon = '_static/favicon.png'
html_css_files = ['https://use.typekit.net/zfv6aax.css', 'custom.css']
html_title = 'Wiki Documentation'
html_file_suffix = '.html'

htmlhelp_basename = project

# Additional options
html_context = {
    'default_mode': 'dark',
}
html_theme_options = {
    # Logo variants for light/dark mode, shown beside the project title
    'logo': {
        'text': 'Wiki',
        'image_light': '_static/logo-light.png',
        'image_dark': '_static/logo-dark.png',
    },
    # Show previous/next at page bottom
    'show_prev_next': True,
    # Search bar options
    'search_bar_text': 'Search the docs ...',
    # Navigation options
    'navigation_with_keys': True,
    # Show more levels of the in-page TOC by default
    'show_toc_level': 2,
    # Configure navbar menu item alignment
    'navbar_align': 'content',
    # Enable 'Edit this Page' button (see configuration below)
    'use_edit_page_button': False,
    # Icons with links to show at top of page
    'icon_links': [
        {
            'name': 'GitHub',
            'url': 'https://github.com/plasma-ai/wiki',
            'icon': 'fa-brands fa-github',
            'type': 'fontawesome',
        },
    ],
    # Other links without icons
    'external_links': [],
    # Keep the footer to the copyright line
    'footer_start': ['copyright'],
    'footer_end': [],
}
