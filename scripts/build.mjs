import { mkdir, cp, writeFile, readdir } from 'node:fs/promises';
import { resolve, relative } from 'node:path';
const root = resolve(import.meta.dirname, '..');
const dest = resolve(root, 'dist');
await mkdir(dest, { recursive: true });
// Explicit publication allowlist; analysis files and source bundles stay out.
for (const name of ['index.html', 'app.js', 'style.css', 'data', 'assets', 'robots.txt', 'sitemap.xml']) {
  await cp(resolve(root, name), resolve(dest, name), { recursive: true });
}
await writeFile(resolve(dest, '.nojekyll'), '');
const files = await readdir(dest, { recursive: true, withFileTypes: true });
const forbidden = files.filter(f => /\.(glb|bundle|bytes|dll|exe|pack)$/i.test(f.name));
if (forbidden.length) throw new Error('Unexpected raw/model payload in public stage');
console.log(`Built ${relative(root, dest)}/ with ${files.filter(f => f.isFile()).length} public files`);
