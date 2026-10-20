# Changes in v10 (from v9)

* **Change my password**: every signed-in user (viewer, manager, admin) can change their own password from the sidebar
  (needs the current password, signs out their other devices).
* **Forgotten administrator password**: set `ADMIN_RESET_PASSWORD` (optional `ADMIN_RESET_USERNAME`) in the host's secrets and
  restart; applied once per value. See DEPLOY.md.
* **Sign-in boxes** are marked `username` / `current-password`, so Chrome offers the saved login instead of repeatedly
  suggesting a strong password.
* DEPLOY.md explains why a full refresh signs you out on Streamlit Community Cloud (it filters cookies at its proxy).
* Tests: 48 pass (also on PostgreSQL).

# Changes in v9 (from v8)

* **Ready for free cloud hosting (Streamlit Community Cloud + Neon Postgres).** The PostgreSQL driver is now in `requirements.txt`,
  and a database link copied from Neon/Supabase/Heroku (`postgresql://...`) is accepted as it is.
* The whole test suite (41 tests) also passes against a real PostgreSQL 16, in development and in production mode.
* DEPLOY.md: step-by-step section for Streamlit Community Cloud + Neon.

# Changes in v8 (from v7)

* **Saving never moves you to another screen.** After "Submit & Save Invoice" you stay on the New Invoice screen with a fresh,
  empty form. After "Save changes" on an edit you stay on that invoice (reloaded, so you can save again). "Cancel editing" also
  stays on the same screen. Only buttons you press to go somewhere ("Edit invoice", "New invoice for this client",
  "Go to the dashboard") change the screen. Guarded by `tests/test_stay_on_screen.py`.

# Changes in v7 (from v6)

* **Viewer role** now sees only the dashboard (as documented in README "Roles"), enforced in the sidebar AND in page routing.
* Removed 3 obsolete sentences from `locales/ur.json` and `locales/ar.json`.
* `pd.concat(..., sort=True)` in `reportcalc.py`; removed unused imports and variables.
* Tests: stale-cache fix in the filter-persistence test, Streamlit-version-tolerant query-param check.
