#!/usr/bin/env node
/*
 * Slopstopper portable static server, bundled inside slopstopper-cli
 * and run by `slopstopper serve`. Used by the local dynamic-check loop
 * (smoke, accessibility, broken-links, SEO, CWV). Adopters who
 * need to customise can eject with `slopstopper templates eject
 * server.js` and edit `.ss/server.js`; the CLI's `serve` subcommand
 * prefers the override.
 *
 * Usage:
 *   slopstopper serve                                # auto-detect SERVE_ROOT + headers
 *   SERVE_ROOT=dist/client slopstopper serve         # explicit root
 *   PORT=8000 slopstopper serve                      # alternative port
 *   SS_SERVER_HEADERS=public/_headers slopstopper serve   # explicit headers file
 *
 * Auto-detect probes ./dist/client, ./dist, ./build, ./out, ./public,
 * ./app in that order and picks the first one containing index.html.
 * Pretty URLs: /foo → /foo.html or /foo/index.html (whichever exists).
 *
 * Headers (optional). Both Cloudflare/Netlify native `_headers` text
 * format and the `worker/headers.json` JSON shape are
 * supported, picked by extension or file content. Auto-detect probes
 * (in order): `worker/headers.json`, `public/_headers`. Set
 * SS_SERVER_HEADERS to point at a specific file. Pattern syntax
 * matches Cloudflare's headers rules: exact paths, `/prefix/*`, `/*`.
 *
 * Known gap vs Cloudflare's edge: this server doesn't strip headers
 * Cloudflare strips automatically (e.g. `Server`). For header-audit purposes
 * the parity is close enough, because the security headers we care
 * about (CSP, X-Frame-Options, COOP/CORP, etc.) are what the scanner checks.
 */

'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = parseInt(process.env.PORT || '8080', 10);
const CWD = process.cwd();


function findServeRoot() {
	if (process.env.SERVE_ROOT) {
		const abs = path.resolve(CWD, process.env.SERVE_ROOT);
		if (!fs.existsSync(abs)) {
			console.error(`SERVE_ROOT=${process.env.SERVE_ROOT} does not exist (resolved to ${abs})`);
			process.exit(1);
		}
		return abs;
	}
	const candidates = ['dist/client', 'dist', 'build', 'out', 'public', 'app'];
	for (const candidate of candidates) {
		const abs = path.resolve(CWD, candidate);
		if (fs.existsSync(path.join(abs, 'index.html'))) return abs;
	}
	console.error(`Could not auto-detect SERVE_ROOT. Tried: ${candidates.join(', ')}`);
	console.error('Set SERVE_ROOT explicitly, e.g. SERVE_ROOT=dist/client slopstopper serve');
	process.exit(1);
}


function findHeadersFile() {
	if (process.env.SS_SERVER_HEADERS) {
		const abs = path.resolve(CWD, process.env.SS_SERVER_HEADERS);
		if (!fs.existsSync(abs)) {
			console.error(`SS_SERVER_HEADERS=${process.env.SS_SERVER_HEADERS} does not exist (resolved to ${abs})`);
			process.exit(1);
		}
		return abs;
	}
	// Auto-detect in priority order. worker/headers.json first because
	// it is the Worker's own shape; public/_headers second because
	// it's the Cloudflare/Netlify native format every adopter on those
	// platforms already has.
	for (const candidate of ['worker/headers.json', 'public/_headers']) {
		const abs = path.resolve(CWD, candidate);
		if (fs.existsSync(abs)) return abs;
	}
	return null;
}


function pickParser(headersPath) {
	// Sniff by extension first (cheap, unambiguous for the two formats
	// we ship). Fall back to inspecting first non-comment line: JSON
	// arrays start with `[`; Cloudflare _headers starts with a path
	// pattern (`/`).
	if (headersPath.endsWith('.json')) return parseJsonRules;
	const lower = headersPath.toLowerCase();
	if (lower.endsWith('_headers') || lower.includes('/_headers')) return parseCloudflareHeaders;
	try {
		const text = fs.readFileSync(headersPath, 'utf8');
		const firstSignificant = text.split('\n').map(l => l.trim()).find(l => l && !l.startsWith('#'));
		if (firstSignificant && firstSignificant.startsWith('[')) return parseJsonRules;
	} catch (_) {
		/* fall through to cloudflare default */
	}
	return parseCloudflareHeaders;
}


function parseJsonRules(headersPath) {
	try {
		const parsed = JSON.parse(fs.readFileSync(headersPath, 'utf8'));
		if (!Array.isArray(parsed)) {
			console.warn(`Warning: ${headersPath} is not an array, so no headers will be applied`);
			return [];
		}
		return parsed.filter(r => r && typeof r.for === 'string' && r.values && typeof r.values === 'object');
	} catch (e) {
		console.warn(`Warning: could not read ${headersPath} (${e.message}), so no headers will be applied`);
		return [];
	}
}


function _headerBlockLines(block) {
	return block
		.split('\n')
		.map(l => l.replace(/\r$/, ''))
		.filter(l => l.trim() && !l.trim().startsWith('#'));
}


function _parseHeaderEntries(lines) {
	// Lines after the path pattern are `Name: value` pairs. Skip
	// entries without a colon or with an empty name.
	const values = {};
	for (let i = 1; i < lines.length; i++) {
		const colon = lines[i].indexOf(':');
		if (colon === -1) continue;
		const name = lines[i].slice(0, colon).trim();
		if (!name) continue;
		values[name] = lines[i].slice(colon + 1).trim();
	}
	return values;
}


function _parseHeaderBlock(block) {
	// Returns one {for, values} rule, or null if the block doesn't
	// open with a path pattern or has no header entries.
	const lines = _headerBlockLines(block);
	if (lines.length === 0) return null;
	const pattern = lines[0].trim();
	if (!pattern.startsWith('/')) return null;
	const values = _parseHeaderEntries(lines);
	if (Object.keys(values).length === 0) return null;
	return { for: pattern, values };
}


function parseCloudflareHeaders(headersPath) {
	// Cloudflare/Netlify _headers grammar:
	//   * Blank lines separate rule blocks.
	//   * Lines starting with `#` are comments (whole-line only).
	//   * First non-comment line of a block is the path pattern
	//     (e.g. `/*`, `/foo`, `/blog/*`).
	//   * Subsequent indented lines are `Name: value` headers.
	// Per-block parsing is delegated so this function stays under
	// the CCN-10 cap enforced by `ss:hygiene:complexity`.
	let text;
	try {
		text = fs.readFileSync(headersPath, 'utf8');
	} catch (e) {
		console.warn(`Warning: could not read ${headersPath} (${e.message}), so no headers will be applied`);
		return [];
	}
	const rules = [];
	for (const block of text.split(/\n\s*\n/)) {
		const rule = _parseHeaderBlock(block);
		if (rule) rules.push(rule);
	}
	return rules;
}


function loadHeaderRules(headersPath) {
	if (!headersPath) return [];
	const parser = pickParser(headersPath);
	return parser(headersPath);
}


function matchesPattern(pattern, urlPath) {
	if (pattern === '/*') return true;
	if (!pattern.endsWith('/*')) return pattern === urlPath;
	const prefix = pattern.slice(0, -1); // strip trailing *
	return urlPath.startsWith(prefix);
}


function headersForPath(rules, urlPath) {
	const result = {};
	for (const rule of rules) {
		if (matchesPattern(rule.for, urlPath)) {
			Object.assign(result, rule.values);
		}
	}
	return result;
}


// Module-level constants the request handler closes over. Initialised
// inside the require.main guard so `require('./server.js')` from a test
// harness doesn't trigger SERVE_ROOT detection (which exits 1 when
// CWD has no build output: fine for `slopstopper serve`, fatal for
// unit tests of just the parsers).
let SERVE_ROOT, HEADERS_PATH, HEADER_RULES;


const MIME = {
	'.html': 'text/html; charset=utf-8',
	'.js': 'application/javascript',
	'.mjs': 'application/javascript',
	'.css': 'text/css',
	'.json': 'application/json',
	'.png': 'image/png',
	'.jpg': 'image/jpeg',
	'.jpeg': 'image/jpeg',
	'.gif': 'image/gif',
	'.webp': 'image/webp',
	'.svg': 'image/svg+xml',
	'.ico': 'image/x-icon',
	'.txt': 'text/plain; charset=utf-8',
	'.xml': 'application/xml; charset=utf-8',
	'.webmanifest': 'application/manifest+json',
	'.woff': 'font/woff',
	'.woff2': 'font/woff2',
};


function resolveCandidate(urlPath) {
	const stripped = urlPath.endsWith('/') ? urlPath.slice(0, -1) : urlPath;
	const candidates = stripped === ''
		? [path.join(SERVE_ROOT, 'index.html')]
		: [
			path.join(SERVE_ROOT, stripped),
			path.join(SERVE_ROOT, `${stripped}.html`),
			path.join(SERVE_ROOT, stripped, 'index.html'),
		];
	for (const candidate of candidates) {
		try {
			const stat = fs.statSync(candidate);
			if (stat.isFile()) return candidate;
		} catch (_) {
			/* not found, try next */
		}
	}
	return null;
}


const server = http.createServer((req, res) => {
	let urlPath = req.url.split('?')[0];
	try {
		urlPath = decodeURIComponent(urlPath);
	} catch (e) {
		res.writeHead(400);
		res.end('Bad Request');
		return;
	}

	const securityHeaders = headersForPath(HEADER_RULES, urlPath === '' ? '/' : urlPath);

	const target = resolveCandidate(urlPath);
	if (!target) {
		res.writeHead(404, { 'Content-Type': 'text/html; charset=utf-8', ...securityHeaders });
		res.end(`<h1>404 Not Found</h1><pre>${urlPath}</pre>`);
		return;
	}

	let resolved;
	try {
		resolved = fs.realpathSync(target);
	} catch (_) {
		resolved = target;
	}
	if (!resolved.startsWith(SERVE_ROOT + path.sep) && resolved !== SERVE_ROOT) {
		res.writeHead(403, securityHeaders);
		res.end('Forbidden');
		return;
	}

	const ext = path.extname(resolved).toLowerCase();
	const contentType = MIME[ext] || 'application/octet-stream';

	fs.readFile(resolved, (err, data) => {
		if (err) {
			res.writeHead(500, securityHeaders);
			res.end('Internal Server Error');
			return;
		}
		res.writeHead(200, { 'Content-Type': contentType, ...securityHeaders });
		res.end(data);
	});
});


// Only listen when invoked directly via `node server.js`. When the file
// is `require`d by a test harness, the parser functions are reachable
// via module.exports below without spawning a real listener.
if (require.main === module) {
	SERVE_ROOT = findServeRoot();
	HEADERS_PATH = findHeadersFile();
	HEADER_RULES = loadHeaderRules(HEADERS_PATH);
	server.listen(PORT, () => {
		console.log(`Slopstopper server: http://localhost:${PORT}`);
		console.log(`Serving from: ${path.relative(CWD, SERVE_ROOT) || '.'}`);
		if (HEADERS_PATH) {
			console.log(`Applying headers from: ${path.relative(CWD, HEADERS_PATH)} (${HEADER_RULES.length} rule${HEADER_RULES.length === 1 ? '' : 's'})`);
		}
	});
}

module.exports = {
	parseCloudflareHeaders,
	parseJsonRules,
	pickParser,
	loadHeaderRules,
	matchesPattern,
	headersForPath,
};
