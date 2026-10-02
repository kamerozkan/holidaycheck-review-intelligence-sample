# Data notice

This repository contains product samples, not a bulk review dataset.

- The first input file is the exact public Store Example Task input available on 2026-07-28.
- The second input file is an exact owner-side saved Task and is not presented as a public Store Example.
- The third input file is a repository recipe checked against the current Actor input schema.
- The fourth and fifth input files are the exact new public Store example inputs verified on September 30, 2026, build 0.4.16.
- Earlier September verification summaries contain counts and status only. The separately labeled local client-report sample adds numeric hotel and review evidence without complete review text or reviewer identities.
- The output files are based on a successful public Task run and have review text, reviewer identity, owner-response text, and quoted source passages removed or redacted.
- Public review IDs, hotel identifiers, aggregate hotel metadata, ratings, source flags, and numeric signals are retained only to show the data contract and provenance.

The Actor and this repository are unofficial and independent. They are not affiliated with, endorsed by, or supported by HolidayCheck.

Source-provided verified-reservation flags are not independently verified. Observable anomaly signals are investigation hints, not verdicts about fake reviews, fraud, or reviewer intent.

Do not add API keys, webhook secrets, private customer data, private profile data, or full copyrighted review corpora to this repository. Confirm your lawful basis, source permissions, data minimization, retention period, and applicable privacy or database-right obligations before collecting or redistributing data.


## Listing update on September 30, 2026

The Store title, description and search metadata were checked against the owned Actor and synchronized with this repository. This documentation update does not alter executable code, input or output schemas, recorded test outputs, artifact hashes, billing or runtime builds. Existing examples retain their original dates and validation limits. A public listing is not evidence of successful output, network acceptance or an achieved search ranking.

## Local portfolio report sample

`build_client_report.py` is a separate offline client-report builder, not deployed Actor source. Its `holidaycheck-client-report-v1` output does not change the Actor dataset schema.

`client-report-sample/` contains minimal exports from two actual September 30, 2026 owner runs: Dana Beach (`7hddO24Xna0Mt0E32`, build `0.4.16`) and Desert Rose (`TdTIIjrMV52Bav9I0`, build `0.4.17`). Both supplied 20 identified reviews and healthy capped output. Collection timestamps and guest review dates remain separate, with each run's original cap and stop reason retained. Neither collection proves the complete source history, current conditions, customer adoption or comparable hotel performance.

Only public review/hotel IDs, source links, entry and collection dates, numeric ratings/aspect signals, source flags and aggregate hotel metadata are retained in the sample rows. Full review text, titles, source quotations, reviewer details, travel-party data, owner responses, raw contributions, credentials and customer information are omitted. Numeric `MANAGEMENT_REPORT` hotel aggregates are included to demonstrate ingesting that export. Source metadata aggregates have a different population and scale from sampled review averages. No sampled competitor rows, prices, sentiment judgments or hotel rankings are invented.

The local client artifacts exclude full review text and identities even when your local exports contain them. They retain source/review links and local file names/hashes; review IDs and dates may still be personal data in your context. Validate and minimize all reports before redistribution. Keep full Actor exports, private input, keys and customer records outside this public repository. Test-only hotel fixtures are explicitly synthetic and do not appear in the verified client-report sample.

The added `06_two_hotel_portfolio_input.json` and `workflow-release-2026-09-30.json` describe a separate 40-review owner test on build 0.4.18, with 20 sampled reviews per hotel and a delivered German report. Its evidence does not relabel the two older source runs used in `client-report-sample`. No runtime or schema change was made.

## Offline report integrity repair on October 2, 2026

The local report builder and behavioral tests were corrected; the Actor runtime, dataset schema, pricing and recorded collection outputs were not changed. The rebuilt public client-report JSON and HTML retain September 30 collection dates and numeric redacted evidence. Their generation time is the local rebuild time. Missing platform metadata is displayed as unknown, separately from OUTPUT status. Partial or extra exports and timestamp conflicts remain visible. Local file hashes identify inputs but do not authenticate their cloud origin. The replays do not establish current source access, customer adoption or financial performance.
