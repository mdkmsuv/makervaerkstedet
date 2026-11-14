// Build only the standalone pupil entry with upstream's webpack rules/plugins.
// Usage: node /path/to/build-playground.cjs /path/to/packages/scratch-gui
const path = require('node:path');
const {createRequire} = require('node:module');
const gui = path.resolve(process.argv[2] || process.cwd());
process.chdir(gui);
process.env.NODE_ENV = 'production';
process.env.BUILD_TYPE = 'dev';
process.env.CI = '1';
const fromGui = createRequire(path.join(gui, 'package.json'));
const webpack = fromGui('webpack');
const config = fromGui('./webpack.config.js');
config.entry = {guistandalone: config.entry.guistandalone};
config.plugins = config.plugins.filter(plugin =>
    plugin.constructor.name !== 'HtmlWebpackPlugin' ||
    plugin.userOptions.chunks.includes('guistandalone'));
webpack(config, (error, stats) => {
    if (error) { console.error(error); process.exitCode = 1; return; }
    console.log(stats.toString({preset: 'errors-warnings', colors: false}));
    if (stats.hasErrors()) process.exitCode = 1;
});
