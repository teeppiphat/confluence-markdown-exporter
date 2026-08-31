---
title: Troubleshooting
---

# Troubleshooting

## Known issues and limitations

### Missing or duplicated attachment file ID

For some Confluence Server versions / configurations, the attachment file ID is not returned by the API ([#39](https://github.com/Spenhouet/confluence-markdown-exporter/issues/39)).

In that case, `{attachment_file_id}` automatically falls back to the content id. Some
Cloud instances also reuse a `fileId` for different attachment records. The default
[`export.attachment_path`](./configuration/options.md#exportattachment_path) therefore
uses `{attachment_id}`, which is unique. The previous exact default is migrated
automatically.

If you prefer human-readable filenames over numeric IDs, set `export.attachment_path` to use `{attachment_title}{attachment_extension}`, e.g.:

```sh
cme config set export.attachment_path='{space_name}/attachments/{attachment_title}{attachment_extension}'
```

Human-readable titles are not guaranteed unique. Include `{attachment_id}` in custom
templates when overwrite protection is required.

### Export stopped or was interrupted

Re-run the same command. Markdown, attachments, lockfile, failure report, and manifest
writes are atomic; only pages recorded as complete are skipped. Missing local artifacts
are detected and exported again even if the Confluence page version did not change.

If `confluence-failures.json` exists, retry only those scopes with:

```sh
cme retry-failures
```

The command exits `1` while any retry remains unsuccessful and removes the report only
after a fully successful run.

### Another exporter is already writing to the output directory

Only one process can write to one `export.output_path`. Wait for the active export to
finish, or assign a different output directory. The `.cme-export.lock` file may remain
visible after completion; it is a lock target, not evidence that the lock is still held.

### Large attachments

Attachments are downloaded and written in 1 MiB chunks, then atomically moved into
place after the advertised byte size is verified. Ensure the output volume has enough
free space for the existing file plus a temporary replacement during an update.

To avoid downloading unreferenced videos or archives, keep the default:

```sh
cme config set export.attachments_export=referenced
```

Use `all` only when the additional time, bandwidth, and storage are intentional.

### Images exist locally but do not appear in preview

Check that the preview process can read both the Markdown file and its referenced image.
Exporter builds containing commit `6476d77` created atomically written files with
owner-only (`0600`) permissions. Upgrade to a later build; new files then use the normal
system file mode (`0666` filtered by the process umask), while overwrites retain the
destination's existing permission bits.

Existing exports from the affected build do not need to be downloaded again. Their file
permissions can be updated in place according to the access policy of the destination.
For a private workstation whose normal file mode is `0644`, make the files readable with:

```sh
find /absolute/path/to/confluence-export -type f -perm 0600 -exec chmod 0644 {} +
```

### Optional Jira enrichment stops resolving links

Jira credentials are optional. When they are missing or invalid, Jira issue enrichment
is skipped and the Confluence export continues. Disable the lookup explicitly when Jira
summaries are not needed:

```sh
cme config set export.enable_jira_enrichment=false
```

### Connection issues behind proxy or VPN

There might be connection issues if your Confluence Server is behind a proxy or VPN ([#38](https://github.com/Spenhouet/confluence-markdown-exporter/issues/38)). If you experience issues, help to fix this is appreciated.

## Reporting bugs

Open an issue on the [GitHub issue tracker](https://github.com/Spenhouet/confluence-markdown-exporter/issues) and include:

1. Your Confluence flavour and version (Cloud, Server, Data Center)
2. The exact command you ran
3. The full output, ideally with `cme config set export.log_level=DEBUG` enabled
4. A minimal example page (if possible) reproducing the issue
