"""Sphinx docs configuration file."""

# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
from __future__ import annotations

import datetime as dt
import importlib
import os
import sys
import typing
from collections.abc import Callable
from typing import Any, Optional, ParamSpec, TypeVar

if typing.TYPE_CHECKING:
    from sphinx.config import Config

sys.path.insert(0, os.path.abspath('..'))
year = dt.date.today().year

# -- Project information -----------------------------------------------------

project = 'wiki'
author = 'Plasma AI'
copyright = f'{year}: {author}'

# The full version, including alpha/beta/rc tags
release = importlib.import_module('wiki').__version__

# -- Configure extensions ----------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom ones.
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.napoleon',
    'sphinx_autodoc_typehints',
    'sphinx.ext.todo',
    'sphinx.ext.viewcode',
    'sphinx.ext.mathjax',
]

# List packages which will not (in general) be installed
# when generating documentation with Autodoc.
autodoc_mock_imports = []

# Autodoc settings
autodoc_member_order = 'bysource'
autodoc_default_options = {'ignore-module-all': True}
autoclass_content = 'both'
suppress_warnings = ['autodoc.import_object']

# Napoleon settings
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = False
napoleon_use_admonition_for_examples = False
napoleon_use_admonition_for_notes = False
napoleon_use_admonition_for_references = False
napoleon_use_ivar = False
napoleon_use_param = True
napoleon_use_rtype = False
napoleon_type_aliases = None
napoleon_attr_annotations = True

# sphinx-autodoc-typehints settings
always_document_param_types = True


# NOTE: the formatter extends the template settings -- the default spells a
#   type variable as its constructor call, so `Callable[_P, _R]` in the source
#   renders as `Callable[[ParamSpec(_P)], TypeVar(_R)]`
def typehints_formatter(annotation: Any, config: Config) -> Optional[str]:
    """Spell type variables and a ``Callable`` over a ``ParamSpec`` as the source does.

    Returns:
        The RST spelling, or ``None`` for any other annotation, which defers
        to the default formatter.

    """
    # the extension is a docs-group dependency, so pytest's doctest collection
    # of this module never imports it
    from sphinx_autodoc_typehints import format_annotation

    # spell a type variable as its name, unless a bound (a ParamSpec stores an
    # absent one as NoneType), constraints, or a variance qualify it, which
    # only the default's constructor call shows
    if isinstance(annotation, (TypeVar, ParamSpec)):
        bound = annotation.__bound__ not in (None, type(None))
        constrained = bool(getattr(annotation, '__constraints__', ()))
        variant = annotation.__covariant__ or annotation.__contravariant__
        if not (bound or constrained or variant):
            return f'``{annotation.__name__}``'
    # spell a Callable over a bare ParamSpec unbracketed, where the default
    # lists the ParamSpec as one parameter type
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if (origin is Callable) and args and isinstance(args[0], ParamSpec):
        params, result = (format_annotation(arg, config) for arg in args)
        return f'{format_annotation(Callable, config)}\\ \\[{params}, {result}]'
    return None


# Autosummary settings
autosummary_generate = True

# Viewcode settings
viewcode_follow_imported_members = True

# -- General configuration ---------------------------------------------------

# Add any paths that contain templates here, relative to this directory.
templates_path = ['_templates']

# The suffix of source filenames.
source_suffix = '.rst'

# Changed from `master_doc` in Sphinx 4
root_doc = 'index'

# The reST default role (used for this markup: `text`) to use for all documents.
default_role = 'autolink'

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = ['modules.rst']

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
