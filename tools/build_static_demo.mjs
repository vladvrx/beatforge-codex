// Dependency-free build for Vercel; mirrors build_static_demo.py.
import {mkdir, readFile, writeFile, readdir, copyFile, cp} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, 'web');
const output = path.join(root, 'site');
await mkdir(output, {recursive: true});
let html = (await readFile(path.join(source, 'index.html'), 'utf8')).replaceAll('\r\n', '\n');
html = html.replace('<head>', '<head>\n  <script>window.BEATFORGE_STATIC = true;</script>')
  .replaceAll('src="/web/', 'src="').replaceAll('href="/web/', 'href="')
  .replaceAll('"/assets/', '"assets/').replaceAll("'/assets/", "'assets/");
for (const name of await readdir(source)) {
  if (!['.js', '.css'].includes(path.extname(name))) continue;
  const file = path.join(source, name);
  await copyFile(file, path.join(output, name));
  const revision = createHash('sha256').update(await readFile(file)).digest('hex').slice(0, 12);
  html = html.replaceAll(`"${name}"`, `"${name}?v=${revision}"`);
}
await writeFile(path.join(output, 'index.html'), html);
await cp(path.join(source, 'assets'), path.join(output, 'assets'), {recursive: true});
