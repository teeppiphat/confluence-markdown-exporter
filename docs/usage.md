---
title: Usage
---

# Usage

Run the exporter with the desired Confluence page URL or space URL. Execute the console application by typing `confluence-markdown-exporter` (or its shorter alias `cme`) followed by one of the commands `pages`, `pages-with-descendants`, `spaces`, `orgs`, or `config`. Add `--help` to any command for additional information.

All export commands accept one or more URLs as space-separated arguments. Each command also has a singular alias (`page`, `page-with-descendants`, `space`, `org`) that behaves identically.

## Export pages

Export one or more Confluence pages by URL:

```sh
cme pages <page-url>
cme pages <page-url-1> <page-url-2> ...

# Singular alias (identical behaviour):
cme page <page-url>
```

Supported page URL formats:

- Confluence Cloud: `https://company.atlassian.net/wiki/spaces/SPACEKEY/pages/123456789/Page+Title`
- Confluence Cloud (API gateway): `https://api.atlassian.com/ex/confluence/CLOUDID/wiki/spaces/SPACEKEY/pages/123456789/Page+Title`
- Confluence Server (long): `https://wiki.company.com/display/SPACEKEY/Page+Title`
- Confluence Server (short): `https://wiki.company.com/SPACEKEY/Page+Title`
- Confluence Server (param): `https://wiki.company.com/pages/viewpage.action?pageId=123456789`

## Export pages with descendants

Export one or more Confluence pages and all their descendant pages by URL:

```sh
cme pages-with-descendants <page-url>
cme pages-with-descendants <page-url-1> <page-url-2> ...

# Singular alias (identical behaviour):
cme page-with-descendants <page-url>
```

## Export spaces

Export all Confluence pages of one or more spaces by URL:

```sh
cme spaces <space-url>
cme spaces <space-url-1> <space-url-2> ...

# Singular alias (identical behaviour):
cme space <space-url>
```

When multiple space URLs are supplied, their page trees are discovered concurrently
up to `connection_config.space_workers`. Page export remains bounded by
`connection_config.max_workers`.

Supported space URL formats:

- Confluence Cloud: `https://company.atlassian.net/wiki/spaces/SPACEKEY`
- Confluence Cloud (API gateway): `https://api.atlassian.com/ex/confluence/CLOUDID/wiki/spaces/SPACEKEY`
- Confluence Server (long): `https://wiki.company.com/display/SPACEKEY`
- Confluence Server (short): `https://wiki.company.com/SPACEKEY`

## Export all spaces of an organization

Export all Confluence pages across all spaces of one or more organizations by URL:

```sh
cme orgs <base-url>
cme orgs <base-url-1> <base-url-2> ...

# Singular alias (identical behaviour):
cme org <base-url>
```

## Output layout

The exported Markdown file(s) will be saved in the configured output directory (see [`export.output_path`](./configuration/options.md#exportoutput_path)) e.g.:

```text
output_path/
├── MYSPACE/
│  ├── attachments/
│  │  └── att123456.png
│  ├── MYSPACE.md
│  └── MYSPACE/
│     ├── My Confluence Page.md
│     └── My Confluence Page/
│        └── My nested Confluence Page.md
├── confluence-lock.json
└── confluence-manifest.json
```

Attachment downloads are streamed in chunks and moved into place atomically, so a
large image, video, or archive does not need to fit in memory and an interrupted write
does not replace the previous complete file. The default filename uses
`{attachment_id}`, which is unique even when Confluence returns a duplicated `fileId`.

`confluence-manifest.json` lists every exported artifact with its byte size and SHA-256
digest. The lockfile records only fully completed pages and attachments.

## Partial failures and exit status

Page exports continue independently when one page fails. If any page or attachment
fails, the command exits with status `1` after the remaining work finishes and writes
`confluence-failures.json` in the configured output directory. The report contains
sanitized identifiers and error types, but no credentials, response bodies, or raw
exception messages.

Re-run the same export command to retry failed work. Pages recorded as complete in
`confluence-lock.json` are skipped, while failed or incomplete pages are attempted
again. A fully successful run exits with status `0` and removes a stale failure report
from an earlier run.

To retry only the scopes in the report:

```sh
cme retry-failures

# Or use another report filename inside export.output_path
cme retry-failures --report previous-failures.json
```

The report contains a sanitized retry URL without credentials, query parameters, or
fragments. If retry is interrupted, run the command again; each completed page has
already been committed to the lockfile.

## Concurrency and output locking

- `connection_config.max_workers` bounds concurrent page exports (default `20`).
- `connection_config.space_workers` bounds concurrent space discovery (default `4`).
- `DEBUG` logging forces serial operation to make diagnostics readable.
- A process lock prevents two commands from writing to the same `export.output_path`.
  Use different output directories when intentionally running independent exports in
  parallel.
