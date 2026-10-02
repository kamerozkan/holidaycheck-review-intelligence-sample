#!/usr/bin/env python3
"""Build a dated, evidence-linked hotel briefing from local Actor exports.

Standard library only. No network, credentials, AI or customer data collection.
The client-report contract is separate from the Actor dataset contract.
"""

import argparse
import hashlib
import html
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


ASPECTS = {
    "room": "Room", "cleanliness": "Cleanliness", "service": "Service",
    "foodAndBeverage": "Food and beverage", "location": "Location",
    "amenities": "Amenities", "value": "Value",
    "familyAndEntertainment": "Family and entertainment",
}
CAVEATS = [
    "This is a dated collection sample, not a current or complete hotel history.",
    "Sample ratings use the review 1-10 scale. Source hotel ratings use the separate 0-6 scale.",
    "Hotels, periods, sorting and coverage can differ. No hotel ranking or change claim is inferred.",
    "Aspect scores are deterministic source signals on -1 to +1, not independent sentiment judgments.",
    "Source verified-reservation flags are not independent verification of a stay.",
    "Competitor candidate counts do not prove reviews were collected for those hotels.",
    "Full review text, reviewer identities and source quotations are excluded from this client report.",
    "Local file hashes identify saved exports; manifest run labels alone do not authenticate their cloud origin.",
]


def number(value, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or (low is not None and value < low) or (high is not None and value > high):
        return None
    return value


def count(value):
    value = number(value, 0)
    return int(value) if value is not None and int(value) == value else None


def timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def text(value):
    return value if isinstance(value, str) else None


def mapping(value):
    return value if isinstance(value, dict) else {}


def safe_url(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password else None
    except ValueError:
        return None


def hotel_id(row):
    hotel = mapping(row.get("hotel"))
    row_id, nested_id = row.get("hotelId"), hotel.get("id")
    if row_id is not None and nested_id is not None and str(row_id) != str(nested_id):
        raise ValueError("Dataset hotelId conflicts with nested hotel.id.")
    value = row.get("hotelId") or hotel.get("id") or row.get("sourceUrl")
    return str(value).strip() or None if isinstance(value, (str, int)) and not isinstance(value, bool) else None


def read_file(root, filename):
    if not isinstance(filename, str) or not filename:
        raise ValueError("File references must be nonempty local path strings.")
    path = root / filename
    raw = path.read_bytes()
    return json.loads(raw), {"file": filename, "sha256": hashlib.sha256(raw).hexdigest()}


def dataset_rows(data):
    if isinstance(data, dict):
        if isinstance(data.get("data"), dict):
            data = data["data"]
        if "items" in data:
            data = data["items"]
        elif "reviewId" in data:
            data = [data]
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ValueError("Dataset must be an array of review objects, an items envelope, or one review object.")
    return data


def period(values):
    valid = [(timestamp(value), value) for value in values if timestamp(value)]
    return {"from": min(valid)[1] if valid else None, "to": max(valid)[1] if valid else None}


def rate(rows, field):
    values = [row[field] for row in rows if isinstance(row.get(field), bool)]
    return {"positive": sum(values), "knownCount": len(values),
            "rate": round(sum(values) / len(values), 4) if values else None}


def source_summary(rows, warnings):
    result = {}
    fields = {"rating6": (0, 6), "recommendationRate": (0, 100), "reviewCount": (0, None)}
    for field, bounds in fields.items():
        values = {number(mapping(row.get("hotel")).get(field), *bounds) for row in rows}
        values.discard(None)
        if len(values) == 1:
            result[field] = values.pop()
        else:
            result[field] = None
            if len(values) > 1:
                warnings.append("Conflicting source hotel %s values; the aggregate is unavailable." % field)
    result["basis"] = "source hotel metadata repeated in dataset rows"
    return result


def sample_summary(rows, report_hotel, warnings):
    """Rows take precedence. Aggregate-only exports retain their reported basis."""
    if rows is None:
        aspects = []
        for item in report_hotel.get("latestAspects") or []:
            if not isinstance(item, dict):
                continue
            aspects.append({"aspect": text(item.get("aspect")), "label": ASPECTS.get(item.get("aspect"), str(item.get("aspect", "Unknown"))),
                            "score": number(item.get("score"), -1, 1), "reviewCount": count(item.get("reviewCount")),
                            "period": text(item.get("month")), "basis": "Actor management report aggregate"})
        return {"basis": "Actor management report aggregate; individual rows unavailable", "reviewCount": count(report_hotel.get("reviewCount")),
                "identifiedReviewCount": None, "duplicateRowsRemoved": None, "ratingCount": None,
                "averageRating10": number(report_hotel.get("averageRating10"), 1, 10),
                "recommendation": {"positive": None, "knownCount": None, "rate": number(report_hotel.get("recommendationRate"), 0, 1)},
                "verifiedReservation": {"positive": None, "knownCount": None, "rate": number(report_hotel.get("verifiedReservationShare"), 0, 1)},
                "period": period([report_hotel.get("firstReviewDate"), report_hotel.get("latestReviewDate")]),
                "aspects": aspects, "reviewEvidence": []}
    unique, seen = [], set()
    duplicates = 0
    for row in rows:
        identity = text(row.get("reviewId"))
        if identity and identity in seen:
            duplicates += 1
            continue
        if identity:
            seen.add(identity)
        unique.append(row)
    if duplicates:
        warnings.append("%d repeated review rows were removed within this hotel/run." % duplicates)
    ratings = [number(row.get("overallRating"), 1, 10) for row in unique]
    valid_ratings = [value for value in ratings if value is not None]
    invalid_ratings = sum(row.get("overallRating") is not None and value is None for row, value in zip(unique, ratings))
    if invalid_ratings:
        warnings.append("%d invalid ratings excluded; valid review rating scale is 1-10." % invalid_ratings)
    missing_ids = sum(not text(row.get("reviewId")) for row in unique)
    if missing_ids:
        warnings.append("%d rows lack review IDs and cannot be deduplicated across runs." % missing_ids)
    invalid_dates = sum(row.get("entryDate") is not None and timestamp(row.get("entryDate")) is None for row in unique)
    if invalid_dates:
        warnings.append("%d invalid review dates excluded from the observed review period." % invalid_dates)
    aspects = []
    for key, label in ASPECTS.items():
        values = [number(mapping(mapping(row.get("aspectScores")).get(key)).get("score"), -1, 1) for row in unique]
        valid = [value for value in values if value is not None]
        aspects.append({"aspect": key, "label": label, "score": round(sum(valid) / len(valid), 3) if valid else None,
                        "reviewCount": len(valid), "basis": "mean of available deterministic aspectScores in sampled rows"})
    summary = {"basis": "deduplicated dataset rows", "reviewCount": len(unique), "identifiedReviewCount": len(seen),
               "duplicateRowsRemoved": duplicates, "ratingCount": len(valid_ratings),
               "averageRating10": round(sum(valid_ratings) / len(valid_ratings), 2) if valid_ratings else None,
               "recommendation": rate(unique, "recommendation"), "verifiedReservation": rate(unique, "verifiedReservation"),
               "period": period([row.get("entryDate") for row in unique]), "aspects": aspects,
               "reviewEvidence": [{"reviewId": text(row.get("reviewId")), "url": safe_url(row.get("reviewUrl")),
                                   "entryDate": row.get("entryDate") if timestamp(row.get("entryDate")) else None,
                                   "rating10": number(row.get("overallRating"), 1, 10)} for row in unique]}
    if count(report_hotel.get("reviewCount")) is not None and report_hotel["reviewCount"] != len(unique):
        warnings.append("Actor report count differs from the local dataset; dataset metrics take precedence.")
    return summary


def collection(output, output_hotel, source_url, run, input_data, local_rows=None):
    config = mapping(output.get("collection"))
    failed_urls = output.get("failedUrls") or []
    failed = any((entry.get("url") if isinstance(entry, dict) else entry) == source_url for entry in failed_urls) if source_url else False
    output_status, platform_status = text(output.get("status")), text(run.get("platformStatus"))
    failures = {"FAILED", "TIMED-OUT", "TIMED_OUT", "ABORTED"}
    run_status = platform_status if platform_status in failures else output_status or platform_status or "unknown"
    mode = text(config.get("mode")) or text(input_data.get("collectionMode"))
    limit = count(config.get("maxReviewsPerHotel"))
    if limit is None:
        limit = count(input_data.get("maxReviewsPerHotel"))
    if failed:
        state = "failed"
    elif run_status in failures:
        state = "partial" if local_rows or output_hotel else "failed"
    elif mapping(output.get("billing")).get("limitReached") is True:
        state = "limited"
    elif number(output.get("failedHotels"), 0) and output_hotel:
        state = "completed_in_partial_run"
    elif run_status == "SUCCEEDED" and output_hotel:
        state = "completed_capped" if mode == "limit" else "completed_public_collection"
    else:
        state = "unknown"
    reported_rows = count(output_hotel.get("reviewsOutput"))
    local_count = None if local_rows is None else len(local_rows)
    completeness = "not_exported" if local_count is None else "unknown"
    if local_count is not None and reported_rows is not None:
        completeness = "matched" if local_count == reported_rows else "incomplete" if local_count < reported_rows else "count_mismatch"
        if completeness != "matched" and state not in {"failed", "partial", "limited"}:
            state = "incomplete_export" if completeness == "incomplete" else "inconsistent_export"
    return {"status": state, "runStatus": run_status, "platformStatus": platform_status, "outputStatus": output_status,
            "datasetCompleteness": completeness, "localDatasetRows": local_count,
            "healthStatus": text(mapping(output.get("health")).get("status")),
            "mode": mode, "maxReviewsPerHotel": limit, "sort": text(input_data.get("sort")),
            "cutoffDate": config.get("cutoffDate") or input_data.get("cutoffDate"),
            "stopReason": text(output_hotel.get("stoppedBecause")), "pagesFetched": count(output_hotel.get("pagesFetched")),
            "sourceReportedReviewCount": count(output_hotel.get("totalAvailable")),
            "publicIndexedReviewCount": count(output_hotel.get("indexedReviewsCount")),
            "reportedRowsOutput": reported_rows,
            "completeSourceHistory": False}


def build_report(manifest, root=Path("."), generated_at=None):
    if not isinstance(manifest, dict) or not isinstance(manifest.get("runs"), list) or not manifest["runs"]:
        raise ValueError("Manifest requires at least one run.")
    if generated_at is not None and not timestamp(generated_at):
        raise ValueError("generatedAt must be a valid report timestamp.")
    hotels = {}
    run_records, global_warnings, all_reviews = [], [], set()
    unidentified_rows = total_duplicate_rows = 0
    seen_runs = set()
    for run in manifest["runs"]:
        if not isinstance(run, dict) or not text(run.get("runId")):
            raise ValueError("Each manifest run requires a nonempty runId.")
        run_id = run["runId"]
        if run_id in seen_runs:
            raise ValueError("Each runId must occur once in the manifest.")
        seen_runs.add(run_id)
        documents, provenance = {}, {}
        for key in ("datasetFile", "outputFile", "reportFile", "inputFile"):
            if run.get(key) is not None:
                documents[key], provenance[key] = read_file(Path(root), run[key])
        if not any(key in documents for key in ("datasetFile", "outputFile", "reportFile")):
            raise ValueError("Each run requires a datasetFile, outputFile or reportFile.")
        output = mapping(documents.get("outputFile"))
        report = mapping(documents.get("reportFile"))
        input_data = mapping(documents.get("inputFile"))
        # Manifest dates must not relabel an older collection as new evidence.
        observed = output.get("finishedAt") or report.get("generatedAt") or run.get("observedAt")
        if not timestamp(observed):
            raise ValueError("Run %s requires a valid observedAt or output/report timestamp." % run_id)
        if run.get("observedAt") is not None and not timestamp(run["observedAt"]):
            raise ValueError("Run %s requires a valid observedAt." % run_id)
        if output.get("startedAt") is not None:
            started = timestamp(output["startedAt"])
            if not started or started > timestamp(observed):
                raise ValueError("Run %s has invalid output collection chronology." % run_id)
        for document in (output, report):
            if document.get("runId") is not None and document["runId"] != run_id:
                raise ValueError("Output/report runId conflicts with manifest runId.")
        if timestamp(run.get("observedAt")) and abs((timestamp(run["observedAt"]) - timestamp(observed)).total_seconds()) > 5:
            global_warnings.append("Run %s manifest observedAt differs from the export; the export collection timestamp takes precedence." % run_id)
        grouped = defaultdict(list)
        rows = dataset_rows(documents["datasetFile"]) if "datasetFile" in documents else None
        for row in rows or []:
            identity = hotel_id(row)
            if not identity:
                raise ValueError("Every dataset row requires hotelId, hotel.id or sourceUrl.")
            grouped[identity].append(row)
        output_hotels, report_hotels, expected_hotels = {}, {}, {}
        for target, values in ((output_hotels, output.get("hotels")), (report_hotels, report.get("hotels")), (expected_hotels, run.get("expectedHotels"))):
            if values is not None and not isinstance(values, list):
                raise ValueError("Hotel lists must be arrays.")
            for item in values or []:
                if not isinstance(item, dict) or not hotel_id(item):
                    raise ValueError("Every hotel summary requires an identity.")
                if hotel_id(item) in target:
                    raise ValueError("Duplicate hotel identities in a run summary are ambiguous.")
                target[hotel_id(item)] = item
        ids = sorted(set(grouped) | set(output_hotels) | set(report_hotels) | set(expected_hotels))
        record = {"runId": run_id, "buildNumber": text(run.get("buildNumber")), "observedAt": observed,
                  "evidenceUrl": safe_url(run.get("evidenceUrl")) or "https://console.apify.com/actors/runs/" + run_id,
                  "files": provenance, "hotelCount": len(ids), "platformStatus": text(run.get("platformStatus")) or "unknown",
                  "outputStatus": text(output.get("status")) or "unknown"}
        run_records.append(record)
        if not ids:
            global_warnings.append("Run %s contains no attributable hotel data; platform status is %s and OUTPUT status is %s." % (run_id, record["platformStatus"], record["outputStatus"]))
        for identity in ids:
            actual_rows = grouped.get(identity, []) if rows is not None else None
            output_hotel, report_hotel, expected = output_hotels.get(identity, {}), report_hotels.get(identity, {}), expected_hotels.get(identity, {})
            first_row = (actual_rows or [{}])[0]
            source_url = safe_url(output_hotel.get("sourceUrl") or first_row.get("sourceUrl") or expected.get("sourceUrl"))
            name = text(output_hotel.get("hotelName")) or text(first_row.get("hotelName")) or text(report_hotel.get("hotelName")) or text(expected.get("hotelName")) or identity
            warnings = []
            summary = sample_summary(actual_rows, report_hotel, warnings)
            future_rows = sum(timestamp(row.get("entryDate")) > timestamp(observed) for row in actual_rows or [] if timestamp(row.get("entryDate")))
            if future_rows:
                warnings.append("%d review entry dates are after the collection timestamp; these inconsistent dates do not prove later collection." % future_rows)
            state = collection(output, output_hotel, source_url, run, input_data, actual_rows)
            if state["status"] in ("failed", "partial", "limited", "completed_in_partial_run", "unknown", "incomplete_export", "inconsistent_export"):
                warnings.append("Collection status is %s; retained rows do not prove complete collection." % state["status"])
            if state["datasetCompleteness"] in ("incomplete", "count_mismatch"):
                warnings.append("Local export contains %d rows; OUTPUT reports %d. Dataset coverage is %s." % (state["localDatasetRows"], state["reportedRowsOutput"], state["datasetCompleteness"]))
            if state["platformStatus"] and state["outputStatus"] and state["platformStatus"] != state["outputStatus"]:
                warnings.append("Platform and OUTPUT statuses conflict; a failed platform run is not treated as completed.")
            if state["healthStatus"] not in (None, "healthy"):
                warnings.append("Actor health status is %s." % state["healthStatus"])
            if state["reportedRowsOutput"] is not None and summary["reviewCount"] is not None and state["reportedRowsOutput"] != summary["reviewCount"]:
                warnings.append("OUTPUT row count differs from local sample count.")
            if any(item["reviewCount"] is not None and 0 < item["reviewCount"] < 5 for item in summary["aspects"]):
                warnings.append("Some aspect scores have fewer than five supporting reviews; interpret their coverage carefully.")
            source = source_summary(actual_rows or [], warnings)
            if source["reviewCount"] is None:
                source["reviewCount"] = state["sourceReportedReviewCount"]
                source["basis"] = "source hotel metadata where available; OUTPUT source count fallback"
            observation = {"runId": run_id, "observedAt": observed, "buildNumber": record["buildNumber"],
                           "sourceUrl": source_url, "evidenceUrl": record["evidenceUrl"], "collection": state,
                           "sample": summary, "sourceHotelSummary": source,
                           "competitorCandidateCount": count(mapping(report_hotel.get("competitorPosition")).get("selectedCompetitors")),
                           "warnings": warnings}
            hotel = hotels.setdefault(identity, {"hotelId": identity, "hotelName": name, "observations": []})
            hotel["observations"].append(observation)
            total_duplicate_rows += summary["duplicateRowsRemoved"] or 0
            for row in actual_rows or []:
                review_id = text(row.get("reviewId"))
                if review_id:
                    all_reviews.add((identity, review_id))
            unidentified_rows += (summary["reviewCount"] or 0) - (summary["identifiedReviewCount"] or 0) if rows is not None else 0
    hotel_list = list(hotels.values())
    for hotel in hotel_list:
        hotel["observations"].sort(key=lambda item: (timestamp(item["observedAt"]), item["runId"]), reverse=True)
        hotel["latestObservedAt"] = hotel["observations"][0]["observedAt"]
    hotel_list.sort(key=lambda hotel: hotel["hotelName"].casefold())
    observation_counts = Counter(observation["collection"]["status"] for hotel in hotel_list for observation in hotel["observations"])
    row_occurrences = sum(observation["sample"]["identifiedReviewCount"] or 0 for hotel in hotel_list for observation in hotel["observations"])
    return {"version": "holidaycheck-client-report-v1", "title": text(manifest.get("title")) or "Hotel review briefing",
            "generatedAt": generated_at or datetime.now(timezone.utc).isoformat(),
            "observationsPeriod": period([record["observedAt"] for record in run_records]),
            "portfolio": {"hotelCount": len(hotel_list), "runCount": len(run_records), "observationCount": sum(observation_counts.values()),
                          "identifiedUniqueReviewCount": len(all_reviews), "unidentifiedReviewRows": unidentified_rows,
                          "duplicateRowsWithinRuns": total_duplicate_rows, "repeatedIdentifiedReviewsAcrossRuns": row_occurrences - len(all_reviews),
                          "collectionStatuses": dict(sorted(observation_counts.items())),
                          "summaryOnlyObservationCount": sum(observation["sample"]["identifiedReviewCount"] is None for hotel in hotel_list for observation in hotel["observations"])},
            "hotels": hotel_list, "runs": run_records, "warnings": global_warnings, "caveats": CAVEATS}


def esc(value):
    return html.escape(str(value), quote=True)


def fmt(value, digits=2):
    return "Unavailable" if value is None else (str(value) if isinstance(value, int) else ("%.*f" % (digits, value)).rstrip("0").rstrip("."))


def date_label(value):
    parsed = timestamp(value)
    return parsed.strftime("%d %b %Y") if parsed else "Unavailable"


def link(url, label):
    safe = safe_url(url)
    return '<a href="%s" rel="noopener noreferrer">%s</a>' % (esc(safe), esc(label)) if safe else esc(label)


def rate_label(item):
    value = item.get("rate")
    if value is None:
        return "Unavailable"
    prefix = "%s%%" % fmt(value * 100, 1)
    return prefix + (" (%d/%d known)" % (item["positive"], item["knownCount"]) if item.get("knownCount") is not None else " (report aggregate)")


def render_html(report):
    p = report["portfolio"]
    cards = []
    for hotel in report["hotels"]:
        observations = []
        for i, observation in enumerate(hotel["observations"]):
            sample, source, state = observation["sample"], observation["sourceHotelSummary"], observation["collection"]
            aspects = []
            for item in sample["aspects"]:
                value = item["score"]
                width = 0 if value is None else abs(value) * 50
                left = 50 if value is None or value >= 0 else 50 - width
                shade = "positive" if value is not None and value >= 0 else "negative"
                aspects.append('<tr><th scope="row">%s</th><td class="aspect"><span class="track"><span class="fill %s" style="left:%.2f%%;width:%.2f%%"></span></span></td><td>%s</td><td>%s</td></tr>' % (esc(item["label"]), shade, left, width, esc(fmt(value, 3)), esc(fmt(item["reviewCount"]))))
            evidence = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (link(row["url"], row["reviewId"] or "Review without ID"), esc(date_label(row["entryDate"])), esc(fmt(row["rating10"]))) for row in sample["reviewEvidence"])
            warnings = ''.join('<li>%s</li>' % esc(message) for message in observation["warnings"])
            label = "Latest collection" if i == 0 else "Earlier / parallel collection"
            collection_line = "Mode %s · maxReviewsPerHotel %s · sort %s · stop reason %s" % (state["mode"] or "unknown", fmt(state["maxReviewsPerHotel"]), state["sort"] or "unknown", state["stopReason"] or "unknown")
            review_period = "%s to %s" % (date_label(sample["period"]["from"]), date_label(sample["period"]["to"]))
            source_recommendation = "Unavailable" if source["recommendationRate"] is None else fmt(source["recommendationRate"], 2) + "%"
            observations.append('''<section class="observation"><div class="observation-head"><div><p class="eyebrow">%s</p><h3>Observed %s</h3></div><span class="status">%s</span></div>
<p class="meta">Review entry dates: %s<br>%s</p>
<div class="metrics"><div><span>Sampled reviews</span><strong>%s</strong></div><div><span>Sample mean / 10</span><strong>%s</strong><small>%s ratings available</small></div><div><span>Sample recommendation</span><strong>%s</strong></div></div>
<p class="meta">Metric basis: %s. Source verified-reservation share: %s.</p>
<div class="source-box"><strong>Source hotel summary, a separate scope</strong><p>Source rating %s / 6 · source recommendation %s · source-reported reviews %s · public indexed reviews %s.</p><small>These source aggregates are not calculated from the sampled reviews.</small></div>
<h4>Aspect coverage in this sample</h4><p class="meta">Deterministic scores, -1 to +1. The last column counts reviews with an available score.</p><div class="table-scroll"><table><thead><tr><th>Aspect</th><th>Signal scale</th><th>Mean</th><th>Reviews</th></tr></thead><tbody>%s</tbody></table></div>
%s
<div class="evidence-links">%s · %s · Build %s</div>
<details><summary>Review evidence (%s entries)</summary><div class="table-scroll"><table><thead><tr><th>Source review</th><th>Entry date</th><th>Rating / 10</th></tr></thead><tbody>%s</tbody></table></div></details>
</section>''' % (esc(label), esc(date_label(observation["observedAt"])), esc(state["status"].replace("_", " ")), esc(review_period), esc(collection_line), esc(fmt(sample["reviewCount"])), esc(fmt(sample["averageRating10"])), esc(fmt(sample["ratingCount"])), esc(rate_label(sample["recommendation"])), esc(sample["basis"]), esc(rate_label(sample["verifiedReservation"])), esc(fmt(source["rating6"])), esc(source_recommendation), esc(fmt(source["reviewCount"])), esc(fmt(state["publicIndexedReviewCount"])), ''.join(aspects), '<ul class="warnings">' + warnings + '</ul>' if warnings else '', link(observation["sourceUrl"], "Hotel source"), link(observation["evidenceUrl"], observation["runId"]), esc(observation["buildNumber"] or "unavailable"), len(sample["reviewEvidence"]), evidence))
        cards.append('<article class="hotel"><div class="hotel-head"><span class="hotel-index">%02d</span><div><p class="eyebrow">%s collection observations</p><h2>%s</h2><p class="meta">Hotel ID: %s</p></div></div>%s</article>' % (len(cards) + 1, len(observations), esc(hotel["hotelName"]), esc(hotel["hotelId"]), ''.join(observations)))
    caveats = ''.join('<li>%s</li>' % esc(item) for item in report["caveats"])
    global_warnings = ''.join('<li>%s</li>' % esc(item) for item in report["warnings"])
    runs = ''.join('<tr><td>%s</td><td>%s</td><td>%s<br><small>OUTPUT %s</small></td><td>%s</td></tr>' % (link(run["evidenceUrl"], run["runId"]), esc(run["observedAt"]), esc(run["platformStatus"]), esc(run.get("outputStatus") or "unknown"), '<br>'.join(esc(Path(item["file"]).name) + '<br><small class="hash">SHA-256 ' + esc(item["sha256"]) + '</small>' for item in run["files"].values())) for run in report["runs"])
    summary = "%d hotel(s), %d collection observation(s), %d identified unique sampled review(s)." % (p["hotelCount"], p["observationCount"], p["identifiedUniqueReviewCount"])
    if p["repeatedIdentifiedReviewsAcrossRuns"]:
        summary += " %d review appearances repeat across runs and are counted once in the portfolio total." % p["repeatedIdentifiedReviewsAcrossRuns"]
    if p["summaryOnlyObservationCount"]:
        summary += " %d observation(s) contain report aggregates only; their review identities are unavailable." % p["summaryOnlyObservationCount"]
    if p["unidentifiedReviewRows"]:
        summary += " %d row appearances have no review ID and are excluded from the identified unique total." % p["unidentifiedReviewRows"]
    status_text = "; ".join("%s: %d" % (key.replace("_", " "), value) for key, value in p["collectionStatuses"].items()) or "No hotel collection status available"
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>%s</title>
<style>
:root{--ink:#1d2937;--muted:#5b6774;--line:#dce4e8;--paper:#fff;--accent:#137b73;--warm:#f1f5f2}*{box-sizing:border-box}body{margin:0;background:#eef2f3;color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{max-width:1120px;margin:38px auto;padding:0 26px 42px}a{color:#086c65;text-decoration-thickness:1px;text-underline-offset:3px}header{background:#142d3a;color:white;border-radius:20px;padding:40px 44px;position:relative;overflow:hidden}header:after{content:"";width:160px;height:160px;border:30px solid #25545c;border-radius:50%%;position:absolute;right:-65px;top:-70px;opacity:.55}header .eyebrow{color:#9ed7cc}h1{font-size:36px;line-height:1.15;max-width:850px;margin:12px 0 16px;letter-spacing:-1px}h2{font-size:25px;line-height:1.25;margin:4px 0}h3{font-size:18px;margin:2px 0}h4{font-size:16px;margin:24px 0 4px}p{margin:8px 0}.eyebrow{text-transform:uppercase;letter-spacing:1.5px;font-size:11px;font-weight:750;margin:0}.hero-meta{color:#c9dce2;font-size:13px}.brief{display:grid;grid-template-columns:1.6fr 1fr;gap:20px;margin:24px 0}.panel{border:1px solid var(--line);background:white;padding:24px;border-radius:16px}.panel h2{font-size:19px}.panel p{font-size:14px}.status-line{font-size:12px;color:var(--muted)}.hotel{background:var(--paper);border:1px solid var(--line);border-radius:18px;margin:26px 0;overflow:hidden}.hotel-head{padding:27px 30px;display:flex;gap:18px;background:var(--warm);border-bottom:1px solid var(--line)}.hotel-index{color:var(--accent);font-size:32px;line-height:1.3;font-weight:750}.meta{color:var(--muted);font-size:12px;overflow-wrap:anywhere}.observation{padding:26px 30px;border-bottom:1px solid var(--line)}.observation:last-child{border:0}.observation-head{display:flex;justify-content:space-between;align-items:center;gap:16px}.status{font-size:11px;border:1px solid #c8d8d6;color:#365854;padding:5px 10px;border-radius:30px;background:#f3f8f6}.metrics{display:grid;grid-template-columns:repeat(3,1fr);margin:20px 0;background:#f7f9fa;border-radius:12px;padding:18px 22px;gap:20px}.metrics span,.metrics small{display:block;color:var(--muted);font-size:11px}.metrics strong{font-size:23px;font-weight:650;line-height:1.4}.metrics div:last-child strong{font-size:16px}.source-box{border-left:3px solid #b8c6cf;padding:12px 16px;background:#f8fafb;border-radius:0 8px 8px 0;font-size:12px}.source-box p{margin:4px 0}.source-box small{color:var(--muted)}table{border-collapse:collapse;width:100%%;font-size:12px;text-align:left;margin:12px 0}th,td{border-bottom:1px solid var(--line);padding:9px 10px;vertical-align:top}thead th{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.4px}tbody th{font-weight:500}.aspect{width:36%%}.track{height:7px;display:block;background:#eef1f2;position:relative;border-radius:5px;overflow:hidden;margin-top:6px}.track:after{content:"";position:absolute;left:50%%;height:100%%;border-left:1px solid #829199}.fill{height:100%%;position:absolute}.positive{background:#53a79a}.negative{background:#c59b5a}.warnings{font-size:12px;color:#725420;background:#fffbf1;padding:14px 18px 14px 34px;border-radius:9px}.evidence-links{font-size:11px;color:var(--muted);margin:18px 0 8px;overflow-wrap:anywhere}details{border-top:1px solid var(--line);padding-top:10px}summary{cursor:pointer;color:#086c65;font-size:12px}.notes{padding:27px 30px}.notes h2{font-size:19px}.notes li{font-size:13px;color:var(--muted);padding:3px 0}.hash{font:9px/1.4 monospace;overflow-wrap:anywhere}.table-scroll{overflow:auto}footer{color:var(--muted);font-size:11px;margin-top:22px}@media(max-width:700px){main{margin:16px auto;padding:0 12px 22px}header{padding:26px 24px}h1{font-size:27px}.brief{grid-template-columns:1fr}.hotel-head,.observation,.notes{padding:22px}.metrics{grid-template-columns:1fr;gap:12px}.observation-head{align-items:flex-start;flex-direction:column}.aspect{min-width:120px}table{min-width:480px}}@media print{body{background:white}main{margin:0;max-width:none;padding:0}header,.hotel,.panel{break-inside:avoid}details{display:block}details>div{display:block}.table-scroll{overflow:visible}a{color:inherit}footer{margin-top:12px}}
</style></head><body><main><header><p class="eyebrow">HolidayCheck · Agency briefing</p><h1>%s</h1><p>Dated evidence for hotel management and portfolio reporting</p><p class="hero-meta">Collections observed %s to %s · Generated %s</p></header>
<div class="brief"><section class="panel"><p class="eyebrow">Management summary</p><h2>What this evidence covers</h2><p>%s</p><p class="status-line">%s</p>%s</section><section class="panel"><p class="eyebrow">Interpretation</p><h2>Read samples in their context</h2><p>Compare sample ratings with their coverage and entry dates. Source hotel aggregates have a different scope. These snapshots do not establish hotel rankings or performance changes.</p><p class="status-line">Capped examples are not complete hotel histories.</p></section></div>
%s<section class="panel notes"><h2>Evidence and collection provenance</h2><p class="meta">Collection timestamp is separate from guest review dates. Console run evidence can require account access. File hashes identify the local exports used.</p><div class="table-scroll"><table><thead><tr><th>Run</th><th>Observed at (UTC)</th><th>Run status</th><th>Local evidence files</th></tr></thead><tbody>%s</tbody></table></div></section><section class="panel notes" style="margin-top:22px"><h2>Scope and limitations</h2><ul>%s</ul></section><footer>Independent reporting tool. Not affiliated with HolidayCheck. No AI conclusions, customer identities or full review text are included.</footer></main></body></html>''' % (esc(report["title"]), esc(report["title"]), esc(date_label(report["observationsPeriod"]["from"])), esc(date_label(report["observationsPeriod"]["to"])), esc(date_label(report["generatedAt"])), esc(summary), esc(status_text), '<ul class="warnings">' + global_warnings + '</ul>' if global_warnings else '', ''.join(cards), runs, caveats)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Local JSON manifest; file references are relative to its directory")
    parser.add_argument("--output-dir", type=Path, default=Path("client-report"))
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        report = build_report(manifest, args.manifest.resolve().parent)
        page = render_html(report)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (args.output_dir / "report.html").write_text(page + "\n", encoding="utf-8")
    except (ValueError, OSError, TypeError) as error:
        parser.exit(2, "Report not built: %s\n" % error)
    print("Built %d hotel(s), %d identified unique sampled review(s).\n%s\n%s" % (report["portfolio"]["hotelCount"], report["portfolio"]["identifiedUniqueReviewCount"], args.output_dir / "report.html", args.output_dir / "report.json"))


if __name__ == "__main__":
    main()
