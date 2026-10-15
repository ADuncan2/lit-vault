# Setting up the Better BibTeX auto-export

The one setup step only you can do: Claude cannot click Zotero's dialogs.
You do it once; afterwards Zotero rewrites the export file whenever your
library changes, and the pipeline reads that file.

1. **Install Better BibTeX** if you haven't:
   <https://retorque.re/zotero-better-bibtex/installation/> — download the
   `.xpi`, then in Zotero: *Tools → Plugins → gear icon → Install Plugin From
   File…*, and restart Zotero.
2. In Zotero's left pane, **right-click "My Library"** → *Export Library…*
   to export everything — or right-click a collection → *Export Collection…*
   if you only want the vault to cover that part of your library.
3. In the export dialog:
   - **Format:** `Better BibTeX JSON` — not "Better BibLaTeX", not "CSL
     JSON". Only this format carries the `zotero://` links, attachments and
     collections the pipeline needs.
   - **Keep updated:** ticked. This is what makes it an auto-export.
   - Leave *Export Notes* and *Export Files* unticked — files are read from
     Zotero's own storage, never copied.
4. Click OK and save it inside this repo's **`Code/Export_data/`** folder,
   under any name ending `.json`. The pipeline uses whichever `.json` file
   it finds there, and stops with an error if there is more than one — so
   keep just the one export in that folder. To keep it somewhere else, put
   its full path in `Code/config.toml` under `[zotero] export`.
5. Check it: *Zotero → Settings → Better BibTeX → Automatic export* should
   list the file, on change.

**A whole library or one collection?** The vault covers only what you
export. Citation gaps still check your whole Zotero database, so papers
in other collections are not listed as missing.

**Citekeys** are Better BibTeX's — every vault file is named by one, so
avoid mass re-keying once the vault is built (it works, but every note is
renamed and links into the vault from your own notes break).

**Troubleshooting.** `python Code/scripts/doctor.py` warns if the export
hasn't been written for 30 days. If it should have been, check the
automatic-export list above; deleting the entry and exporting again with
*Keep updated* ticked fixes most cases.
