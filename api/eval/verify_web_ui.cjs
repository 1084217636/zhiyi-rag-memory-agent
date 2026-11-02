// Run with Playwright installed locally or set PLAYWRIGHT_MODULE to its path.
// This exercises the React test client against a real API, worker and model.
const crypto = require('node:crypto');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const page = await browser.newPage();
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(process.env.ZHIYI_WEB_URL || 'http://127.0.0.1:5174/test-client');
    await page.getByRole('button', { name: '还没有账号？创建一个测试账号' }).click();
    await page.locator('input[autocomplete="username"]').fill('webcheck' + crypto.randomBytes(5).toString('hex'));
    await page.locator('input[autocomplete="new-password"]').fill('Local-Eval-2026!');
    await page.getByRole('button', { name: '注册并继续' }).click();
    await page.getByRole('heading', { name: '连接本地项目' }).waitFor();
    await page.locator('button[type="submit"]').click();
    await page.getByRole('heading', { name: '项目功能验证' }).waitFor({ timeout: 10000 }).catch(async (error) => {
      console.error('registration page:', await page.locator('body').innerText());
      console.error('page errors:', errors);
      throw error;
    });
    console.log('PASS React registration and authenticated page');

    await page.locator('input[type="file"]').setInputFiles(
      path.join(__dirname, 'fixtures/corpus_100/wiki-001.md')
    );
    await page.getByText('wiki-001.md', { exact: true }).waitFor({ timeout: 30000 });
    await page.getByText('可检索', { exact: true }).waitFor({ timeout: 120000 });
    console.log('PASS React upload and Celery completion status');

    await page.locator('.interview-panel-title').filter({ hasText: '2. 会话' }).locator('button').click();
    await page.locator('textarea[placeholder^="输入文本问题"]').fill(
      '请根据我上传的牛顿文档概括他的贡献，并附上引用。'
    );
    await page.locator('.interview-composer button').click();
    await page.locator('.interview-citations span').first().waitFor({ timeout: 120000 });
    await page.getByRole('button', { name: '保存为研究成果' }).click();
    await page.locator('input[placeholder="成果标题"]').fill('牛顿资料整理');
    await page.locator('.ant-modal-footer .ant-btn-primary').click();
    await page.getByText('牛顿资料整理 · v1').waitFor({ timeout: 30000 });
    for (const fact of ['我目前在杭州工作。', '我现在在深圳工作。']) {
      await page.locator('textarea[placeholder^="输入文本问题"]').fill(fact);
      await page.locator('.interview-composer button').click();
      const row = page.locator('.interview-memory').filter({ hasText: fact });
      await row.getByText('已写入图谱', { exact: true }).waitFor({ timeout: 150000 });
    }
    await page.getByText('待确认的冲突记忆', { exact: true }).waitFor({ timeout: 30000 });
    const conflict = page.locator('.interview-memory').filter({ hasText: '深圳' })
      .filter({ has: page.getByRole('button', { name: '替换旧版' }) });
    await conflict.getByRole('button', { name: '替换旧版' }).click();
    await conflict.waitFor({ state: 'hidden' });
    const newSource = page.locator('.interview-memory').filter({ hasText: '我现在在深圳工作。' });
    await newSource.locator('button').click();
    await page.locator('.ant-modal-confirm .ant-btn-primary').click();
    await newSource.waitFor({ state: 'hidden' });
    console.log('PASS React memory conflict confirmation and source deletion');
    if (errors.length) throw new Error(`page errors: ${errors.join('; ')}`);
    console.log('PASS React chat SSE, citation and saved research artifact; no page errors');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
