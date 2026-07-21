"""Constants for ``wiki``."""

#: default wiki folder name assumed by init and root resolution
DEFAULT_WIKI_NAME = 'wiki'
#: control directory declaring a wiki root
WIKI_DIR = '.wiki'
#: cache directory under the control directory
WIKI_CACHE = f'{WIKI_DIR}/cache'
#: settings file under the control directory
WIKI_SETTINGS = f'{WIKI_DIR}/settings.json'
#: index page filename generated at every folder level
WIKI_INDEX = '_index.md'

#: environment variable that disables downloads when set to true
OFFLINE_MODE = 'OFFLINE_MODE'
#: environment variable overriding the user-global config home
WIKI_CONFIG_DIR = 'WIKI_CONFIG_DIR'
