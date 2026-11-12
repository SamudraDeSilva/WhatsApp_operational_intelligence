# Updating the existing GitHub repository to v3.0.0

Repository: https://github.com/SamudraDeSilva/WhatsApp_operational_intelligence

Do not create a second repository, delete the original, or upload a ZIP as a source file. Keep Git history.

## First: security and ownership
- If source ownership or publication permission is not confirmed, set the repository **Private** and obtain written authorization before making it public.
- Do not upload the local `data/` contents, real WhatsApp sessions or messages, employee personal information, credentials or private company spreadsheets.
- The `examples/` database is entirely synthetic. `.gitignore` excludes local databases and reports.

## Recommended update method: GitHub Desktop or Git

1. Back up your working repository before editing it.
2. Clone the existing repository if not already cloned:
   `git clone https://github.com/SamudraDeSilva/WhatsApp_operational_intelligence.git`
3. Open the cloned folder. Extract this update ZIP **elsewhere** first.
4. Copy the contents of `Rail_Operational_Intelligence_v3.0.0_GitHub_Update/` into the existing clone (not the containing folder). Allow replacement of matching `core.py`, `collector.py`, `desktop.py`, `README.md`, and `docs/` files. Preserve `.git` and do not copy any private `data/` files.
5. Verify the new files appear in `git status`, including new `notebooks/Operational_Intelligence_v3.0.0.ipynb`, `CHANGELOG.md`, and `tests/test_v3_0_0_exceptions.py`.
6. On the cloned repository, run:

   ```powershell
   python -m pip install -r requirements.txt
   python -m pip install -r requirements-dev.txt
   python -m pytest -q
   python manage.py init
   python manage.py status
   ```

7. Commit and push (do not force-push):

   ```powershell
   git add -A
   git diff --cached --stat
   git commit -m "feat: release v3.0.0 overview and reporting hub"
   git push origin main
   ```

8. Review the GitHub Actions test run, and manually test the graphical dashboard and browser collector with authorized test data on Windows.
9. To publish an official version, select **Releases → Draft a new release**, choose `v3.0.0` *if it is not already used*, target `main`, add release notes from `RELEASE_NOTES_v3.0.0.md`, and Publish release. If still unverified, select **This is a pre-release** or save a draft.

## Browser-only alternative

- On GitHub, open your existing repository and use **Add file → Upload files**.
- Drag the **contents** of the extracted update folder (files and subfolders), not the ZIP or its enclosing folder.
- GitHub's browser uploader may not apply ignore rules: explicitly review the file list and exclude everything private.
- Make sure existing files are replaced at the *same paths*, not duplicated inside a nested folder.
- After the upload, delete any obsolete notebooks, test files, or migration documents bearing an earlier internal revision label; uploading a renamed file does **not** automatically delete its old GitHub path. Do not delete the original commit history.
- Use the commit message `feat: release v3.0.0 overview and reporting hub` and a description summarizing new exception workflows, account management, exports and tests.
- If GitHub does not offer to replace files conveniently, use GitHub Desktop or `git` as above.

## Version versus commit
A commit records source changes; a GitHub Release tags a chosen commit. Use `v3.0.0` for this release (assuming that tag is unused). The application notebook is named `notebooks/Operational_Intelligence_v3.0.0.ipynb` to match the public release version. Do not create a new repository or overwrite existing Git history.
