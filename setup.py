"""
Setup script to build DevMenu as a standalone macOS application.

Usage:
    python setup.py py2app
"""

# Monkey-patch py2app's tkinter recipe to avoid Tcl initialization failure
import py2app.recipes.tkinter as _tkinter_recipe
_tkinter_recipe.check = lambda cmd, mf: None

# Monkey-patch detect_dunder_file recipe to avoid pulling in setuptools
import py2app.recipes.detect_dunder_file as _dunder_recipe
_dunder_recipe.check = lambda cmd, mf: None

from setuptools import setup

APP = ['dev_menu.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False,
    'iconfile': None,
    'plist': {
        'CFBundleName': 'DevMenu',
        'CFBundleDisplayName': 'DevMenu',
        'CFBundleIdentifier': 'com.shawyu.devmenu',
        'CFBundleVersion': '2.1.0',
        'CFBundleShortVersionString': '2.1.0',
        'LSUIElement': True,
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '12.0',
        'NSAppleScriptEnabled': False,
    },
    'packages': ['objc'],
    'includes': [
        'Quartz',
        'AppKit',
        'Foundation',
    ],
    'excludes': [
        'tkinter',
        '_tkinter',
        'Tkinter',
        'tcl',
        'tk',
        'setuptools',
        'packaging',
        'pkg_resources',
    ],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
