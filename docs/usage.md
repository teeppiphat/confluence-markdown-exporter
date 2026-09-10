---
title: Usage
---

# Usage

Run the exporter with the desired Confluence page URL or space URL. Execute the console application by typing `confluence-markdown-exporter` (or its shorter alias `cme`) followed by one of the commands `pages`, `pages-with-descendants`, `spaces`, `list-spaces`, `orgs`, or `config`. Add `--help` to any command for additional information.

> **สรุปภาษาไทย:** ใช้ `pages` สำหรับหน้าเดียวหรือหลายหน้า,
> `pages-with-descendants` สำหรับหน้าและหน้าลูกทั้งหมด, `spaces` สำหรับทั้ง Space,
> และ `orgs --all-spaces` สำหรับ backup ทุก Space ที่บัญชีเข้าถึงได้ งานขนาดใหญ่ควรเพิ่ม
> `--background` แล้วตรวจด้วย `cme jobs` เพื่อให้งานทำต่อได้แม้ SSH หลุด

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

Export all Confluence pages across current global spaces of one or more organizations:

```sh
cme orgs <base-url>
cme orgs <base-url-1> <base-url-2> ...

# Singular alias (identical behaviour):
cme org <base-url>
```

The space collection is paginated until all current global spaces are found; exports are
not limited to the first 50 entries returned by the SDK.

## List spaces for backup and migration

Create a complete space inventory before a backup or migration:

```sh
# Human-readable table
cme list-spaces https://company.atlassian.net

# Stable machine-readable inventory
cme list-spaces https://company.atlassian.net \
  --format json --output spaces.json

# Spreadsheet/database import
cme list-spaces https://company.atlassian.net \
  --format csv --output spaces.csv
```

Unlike plain `orgs`, which exports current global spaces, `list-spaces` requests the
complete collection exposed by Confluence, including archived and personal spaces. Every
API page is followed and duplicate keys are removed. Each record contains:

- instance base URL and canonical space URL;
- space key and display name;
- space type returned by Confluence (such as global, personal, collaboration, or
  knowledge base) and current/archived status;
- homepage content ID; and
- plain-text space description.

The JSON document includes `schema_version`, UTC `generated_at`, `space_count`, and the
`spaces` array. Keep it with `confluence-manifest.json` and the exported directory as the
inventory layer of a backup. To export selected archived or personal entries when the
source instance permits access, pass their `space_url` values to `cme spaces`.

To export every inventory entry in one backup run, opt in explicitly:

```sh
cme orgs https://company.atlassian.net --all-spaces
```

For a long-running backup that survives an SSH disconnect:

```sh
cme orgs https://company.atlassian.net --all-spaces --background
cme jobs
```

This can be much larger than a normal organization export. Archived and personal spaces
are attempted independently; inaccessible entries are recorded in
`confluence-failures.json` without stopping the remaining backup.

`list-spaces` already inventories every space returned by Confluence, so it does not need
an `--all-spaces` flag. The flag belongs to `orgs`, where it expands the export scope.

## Background jobs

Long-running commands can be queued with `--background` (or `-b`). The command returns a
job ID immediately and a detached worker continues after the terminal or SSH connection
closes:

```sh
cme orgs https://company.atlassian.net --all-spaces --background
cme spaces https://company.atlassian.net/wiki/spaces/SPACEKEY --background
cme list-spaces https://company.atlassian.net \
  --format json --output spaces.json --background
cme retry-failures --background
```

The option is supported by `pages`, `pages-with-descendants`, `spaces`, `list-spaces`,
`orgs`, and `retry-failures`. Jobs use a persistent per-user FIFO queue. One queued command
runs at a time, while the export command's normal page and space worker settings still
provide bounded parallelism within that job. This prevents two queued backups from writing
the same output simultaneously. If a foreground exporter already owns the output lock, the
detached job waits for it instead of failing immediately.

List all retained work, including completed and waiting jobs:

```sh
cme jobs
cme jobs status
```

Inspect one job and follow its output. Closing the log viewer does not stop the job:

```sh
cme jobs status <job-id>
cme jobs logs <job-id>
cme jobs logs --follow <job-id>
```

Job states are `queued`, `starting`, `running`, `succeeded`, `failed`, and `interrupted`.
The status record includes timestamps, attempt number, process ID while running, exit code,
working directory, command, and log path. Credentials are never copied into the job file;
the detached command reads the normal CME configuration. Non-secret export and connection
environment overrides are retained for that job.

The queue and CME configuration are per operating-system user. Submit and inspect jobs as
the same user; running one command with `sudo` creates or reads root's separate queue and
configuration instead.

After a host reboot or unexpected worker termination, requeue stale `running`, `starting`,
and `interrupted` work:

```sh
cme jobs resume
```

Restarted exports use the normal `confluence-lock.json`, so pages and attachments already
committed successfully are skipped. A job with partial export failures has status `failed`
and exit code `1`; inspect its log and `confluence-failures.json`, then queue
`cme retry-failures --background` after resolving the cause.

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
- The background queue is intentionally FIFO and serial across jobs. Parallel page and
  space processing still occurs inside the active job according to the worker settings.
