# Setting up the Better BibTeX auto-export

The one setup step only you can do: Claude cannot click Zotero's dialogs.
You do it once; afterwards Zotero rewrites the export file whenever your
library changes, and the pipeline reads that file.

1. **Install Better BibTeX** if you haven't:
   <https://retorque.re/zotero-better-bibtex/installation/> — download the
   `.xpi`, then in Zotero: *Tools → Plugins → gear icon → Install Plugin From
   File…*, and restart Zotero.
2. In Zotero's left pane, **right-click "My Library"** → *Export Library…*
   (export the whole library, not a collection).
3. In the export dialog:
   - **Format:** `Better BibTeX JSON` — not "Better BibLaTeX", not "CSL
     JSON". Only this format carries the `zotero://` links, attachments and
     collections the pipeline needs.
   - **Keep updated:** ticked. This is what makes it an auto-export.
   - Leave *Export Notes* and *Export Files* unticked — files are read from
     Zotero's own storage, never copied.
4. Click OK and save it as **`library.json`** inside this repo's
   `Code/Export_data/` folder (create the folder if needed). Any other path
   works too — put it in `Code/config.toml` under `[zotero] export`.
5. Check it: *Zotero → Settings → Better BibTeX → Automatic export* should
   list the file, on change.

<!-- Screenshot of the export dialog goes here: docs/img/bbt-export.png -->

**Citekeys** are Better BibTeX's — every vault file is named by one, so
avoid mass re-keying once the vault is built (it works, but every note is
renamed and links into the vault from your own notes break).

**Troubleshooting.** `python Code/scripts/doctor.py` warns if the export
hasn't been written for 30 days. If it should have been, check the
automatic-export list above; deleting the entry and exporting again with
*Keep updated* ticked fixes most cases.
