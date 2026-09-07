const path = require('path');
const CopyPlugin = require('copy-webpack-plugin');

module.exports = (env = {}) => {
  const browser = env.browser ?? 'chrome';
  if (!['chrome', 'edge', 'firefox'].includes(browser)) throw new Error(`Unsupported browser target: ${browser}`);
  return {
    entry: { popup: './popup.js', background: './background.js' },
    output: { filename: '[name].js', path: path.resolve(__dirname, 'dist', browser), clean: true },
    mode: 'production',
    module: {
      rules: [
        {
          test: /\.js$/,
          exclude: /node_modules/,
          use: { loader: 'babel-loader', options: { presets: ['@babel/preset-env'] } },
        },
        { test: /\.css$/, use: ['style-loader', 'css-loader'] },
      ],
    },
    plugins: [
      new CopyPlugin({
        patterns: [
          { from: browser === 'firefox' ? 'manifest.firefox.json' : 'manifest.json', to: 'manifest.json' },
          { from: 'popup.html', to: 'popup.html' },
          { from: 'popup.css', to: 'popup.css' },
          { from: 'icons', to: 'icons' },
        ],
      }),
    ],
  };
};
