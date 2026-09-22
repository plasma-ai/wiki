'use strict';

const js = require('@eslint/js');

const node = { module: 'writable', process: 'readonly', require: 'readonly', __dirname: 'readonly' };
const browser = { Element: 'readonly', document: 'readonly', window: 'readonly' };

module.exports = [
  {
    files: ['wiki/_assets/plugins/**/*.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'commonjs',
      globals: { ...node, ...browser },
    },
    rules: js.configs.recommended.rules,
  },
  {
    files: ['tests/plugin/**/*.js', 'eslint.config.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'commonjs',
      globals: node,
    },
    rules: js.configs.recommended.rules,
  },
];
