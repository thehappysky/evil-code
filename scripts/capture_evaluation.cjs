/* Render an existing local evaluation report; no model calls.
 * Set PLAYWRIGHT_MODULE to an installed Playwright package directory.
 * node scripts/capture_evaluation.cjs RUN_DIRECTORY OUTPUT.png [--viewport]
 */
const fs = require('fs');
const path = require('path');
const {pathToFileURL} = require('url');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '../.pytest-tmp/docs-renderer/node_modules/playwright');

async function main() {
  const [run, output, frame] = process.argv.slice(2);
  if (!run || !output) throw new Error('Expected run directory and PNG output path');
  const browser = await chromium.launch({channel: process.env.DOCS_BROWSER_CHANNEL || 'msedge', headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1200,height:1100},deviceScaleFactor:1});
    await page.goto(pathToFileURL(path.resolve(run, 'report.html')).href);
    await page.evaluate(() => document.fonts.ready);
    const checks = await page.evaluate(() => ({
      title:document.title,
      overflow:document.documentElement.scrollWidth > window.innerWidth,
      rows:document.querySelectorAll('table tr').length,
      categories:document.querySelectorAll('.category').length,
    }));
    if (checks.overflow || !checks.rows || !checks.categories) throw new Error('Report visual structure invalid');
    fs.mkdirSync(path.dirname(path.resolve(output)), {recursive:true});
    await page.screenshot({path:output,fullPage:frame !== '--viewport'});
    console.log(JSON.stringify(checks));
  } finally {
    await browser.close();
  }
}
main().catch(error => {console.error(error);process.exitCode=1;});
