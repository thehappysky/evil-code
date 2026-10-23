/* Local documentation renderer. From the root, install rendering-only deps:
 * npm install --prefix .pytest-tmp/docs-renderer markdown-it mermaid@11 playwright sharp
 * node scripts/render_readme.cjs
 * Uses installed Edge by default; DOCS_BROWSER_CHANNEL can select chrome.
 * PLAYWRIGHT_MODULE can point at an existing Playwright package directory.
 * These packages do not enter the application's Python environment.
 */
const fs = require('fs');
const path = require('path');
const {pathToFileURL} = require('url');
const root = path.resolve(__dirname, '..');
const dependencies = path.join(root, '.pytest-tmp/docs-renderer/node_modules');
const MarkdownIt = require(path.join(dependencies, 'markdown-it'));
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || path.join(dependencies, 'playwright'));
const sharp = require(process.env.DOCS_SHARP_MODULE || path.join(dependencies, 'sharp'));

async function main() {
  const output = path.join(root, '.pytest-tmp/readme-preview');
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({channel: process.env.DOCS_BROWSER_CHANNEL || 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1500, height: 1100}, deviceScaleFactor: 1});
    // The SVG exporter positions terminal cells precisely. The SVG rasterizer
    // preserves those positions with CJK fallback fonts more consistently than
    // a browser's standalone-SVG font substitution.
    await sharp(path.join(root, 'docs/assets/tui-demo.svg')).png().toFile(path.join(root, 'docs/assets/tui-demo.png'));

    const md = new MarkdownIt({html: true});
    const originalFence = md.renderer.rules.fence;
    md.renderer.rules.fence = (tokens, index, options, env, self) => {
      if (tokens[index].info.trim() === 'mermaid') {
        return '<pre class="mermaid">' + md.utils.escapeHtml(tokens[index].content) + '</pre>';
      }
      return originalFence(tokens, index, options, env, self);
    };
    md.renderer.rules.image = (tokens, index, options, env, self) => {
      const token = tokens[index];
      const src = token.attrGet('src');
      if (!/^[a-z]+:/.test(src)) token.attrSet('src', pathToFileURL(path.join(root, src)).href);
      return self.renderToken(tokens, index, options);
    };
    const text = fs.readFileSync(path.join(root, 'README.md'), 'utf8');
    const html = `<!doctype html><meta charset="utf-8"><title>Evil Code README preview</title>
      <style>
      *{box-sizing:border-box}body{margin:0;color:#1f2937;background:#f6f8fa;font:16px/1.75 "Segoe UI","Microsoft YaHei",sans-serif}
      article{max-width:1040px;margin:28px auto;padding:40px 56px;background:white;border:1px solid #d8dee4;border-radius:12px}
      h1{font-size:38px;letter-spacing:-1px}h2{margin-top:48px;padding-bottom:8px;border-bottom:1px solid #d8dee4;font-size:27px;color:#172b4d}
      h3{margin-top:30px}a{color:#0969da;text-decoration:none}img{max-width:100%;height:auto;border-radius:10px}
      table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:10px 13px;text-align:left;border:1px solid #d8dee4;vertical-align:top}
      th{background:#eef3f8}tr:nth-child(2n){background:#f8fafc}pre{padding:17px;background:#f6f8fa;border-radius:8px;overflow:auto;font:13px/1.6 Consolas,monospace}
      code{background:#eff2f5;padding:2px 4px;border-radius:3px}pre code{padding:0;background:none}
      blockquote{border-left:4px solid #8b9cc0;margin:16px 0;padding:4px 18px;color:#576577;background:#f8fafc}
      .mermaid{padding:18px 8px;background:#fbfcfe;border:1px solid #e2e8f0;overflow:visible}.mermaid svg{display:block;margin:auto;max-width:100%;height:auto}
      </style><article>${md.render(text)}</article>`;
    const htmlFile = path.join(output, 'README.html');
    fs.writeFileSync(htmlFile, html);
    await page.setViewportSize({width: 1200, height: 1000});
    await page.goto(pathToFileURL(htmlFile).href);
    await page.addScriptTag({path: path.join(dependencies, 'mermaid/dist/mermaid.min.js')});
    await page.evaluate(async () => {
      mermaid.initialize({startOnLoad: false, securityLevel: 'strict', theme: 'base',
        themeVariables: {fontFamily: 'Segoe UI, Microsoft YaHei, sans-serif', fontSize: '15px',
          primaryColor: '#edf3ff', primaryTextColor: '#19365b', primaryBorderColor: '#7996cf',
          lineColor: '#607694', secondaryColor: '#e8f6f0', tertiaryColor: '#f8f3e7'},
        flowchart: {htmlLabels: false, useMaxWidth: true}, sequence: {useMaxWidth: true}});
      await mermaid.run({querySelector: '.mermaid'});
      await document.fonts.ready;
    });
    const diagrams = page.locator('.mermaid');
    const count = await diagrams.count();
    const names = [...text.matchAll(/<!-- diagram: docs\/diagrams\/([^ ]+)\.mmd -->/g)].map(m => m[1]);
    if (count !== names.length) throw new Error('Diagram source count differs from render count');
    for (let i = 0; i < count; i++) {
      const svg = await diagrams.nth(i).locator('svg').evaluate(node => new XMLSerializer().serializeToString(node));
      fs.writeFileSync(path.join(root, 'docs/assets', names[i] + '.svg'), svg);
      await diagrams.nth(i).screenshot({path: path.join(output, names[i] + '.png')});
    }
    await page.screenshot({path: path.join(output, 'README-full.png'), fullPage: true});
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({path: path.join(output, 'README-top.png')});
    const result = await page.evaluate(() => ({
      images_loaded: [...document.images].every(image => image.complete && image.naturalWidth > 0),
      page_overflow: document.documentElement.scrollWidth > window.innerWidth,
      diagrams_rendered: document.querySelectorAll('.mermaid svg').length,
    }));
    console.log(JSON.stringify(result, null, 2));
    if (!result.images_loaded || result.page_overflow || count !== 4) throw new Error('README visual checks failed');
  } finally {
    await browser.close();
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
