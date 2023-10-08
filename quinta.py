import re
import sys
from collections import defaultdict
from typing import Dict

import googleapiclient
from rich.console import Console
from rich.table import Table
import requests
import google.auth
from google.auth import impersonated_credentials
from googleapiclient.discovery import build, Resource
from functools import cache
from math import sqrt
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.admin import AnalyticsAdminServiceClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)

http = requests.Session()
API_SERVICE_NAME = "webmasters"
API_VERSION = "v3"
SCOPE = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics",
    "https://www.googleapis.com/auth/analytics.edit",
]
SERVICE_ACCOUNT_EMAIL = "quinta@seo-reporter.iam.gserviceaccount.com"


def confidence(clicks, impressions):
    n = impressions
    if n == 0: return 0
    z = 1.96  # 1.96 -> 95% confidence
    phat = float(clicks) / n
    denorm = 1. + (z * z / n)
    enum1 = phat + z * z / (2 * n)
    enum2 = z * sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return (enum1 - enum2) / denorm, (enum1 + enum2) / denorm


def wilson(clicks, impressions):
    if impressions == 0:
        return 0
    else:
        return confidence(clicks, impressions)[0]


def test_up(domain):
    return http.head(f"https://{domain}/").status_code == 200 and "✅" or "❌"


def word_count(domain):
    return http.get(f"https://{domain}/words.txt").text


@cache
def search_perf(domain):
    service = auth_using_impersonation()
    payload = {
        "startDate": "2023-08-25",
        "endDate": "2023-09-22",
    }
    data = service.searchanalytics().query(siteUrl=f"sc-domain:{domain}", body=payload).execute()
    return data["rows"][0]["clicks"], data["rows"][0]["impressions"], data["rows"][0]["position"], wilson(
        data["rows"][0]["clicks"], data["rows"][0]["impressions"]) * 100


@cache
def visits_perf() -> Dict:
    source_credentials, _ = google.auth.default(scopes=SCOPE)
    target_credentials = impersonated_credentials.Credentials(
        source_credentials=source_credentials,
        target_principal=SERVICE_ACCOUNT_EMAIL,
        target_scopes=SCOPE,
        lifetime=3600,
    )
    client = BetaAnalyticsDataClient(credentials=target_credentials)
    visits = defaultdict(int)
    for property in get_google_property():
        request = RunReportRequest(
            property=property,
            dimensions=[Dimension(name="hostName")],
            metrics=[Metric(name="activeUsers")],
            date_ranges=[DateRange(start_date="28daysAgo", end_date="today")],
        )
        response = client.run_report(request)

        for row in response.rows:
            domain, hits = row.dimension_values[0].value, row.metric_values[0].value
            visits[domain] += int(hits)

    visits_by_domain = defaultdict(lambda: 'N/A')
    visits_by_domain.update({
        domain: str(hits or 'N/A')
        for domain, hits in visits.items()
    })
    return visits_by_domain


@cache
def get_google_property():
    source_credentials, _ = google.auth.default(scopes=SCOPE)
    target_credentials = impersonated_credentials.Credentials(
        source_credentials=source_credentials,
        target_principal=SERVICE_ACCOUNT_EMAIL,
        target_scopes=SCOPE,
        lifetime=3600,
    )
    client = AnalyticsAdminServiceClient(credentials=target_credentials)
    for account in client.list_account_summaries():
        for prop in account.property_summaries:
            yield prop.property


@cache
def get_google_tag(domain):
    m = re.search(r"googletagmanager\.com/gtag/js\?id=(G-[^\"]+)", http.get(f"https://{domain}/").text)
    return m and m.group(1) or "❌"


def make_row(domain):
    return (
        domain,
        test_up(domain),
        word_count(domain),
        *[str(int(x)) for x in search_perf(domain)],
        get_google_tag(domain),
        visits_perf()[domain],
    )


@cache
def auth_using_impersonation() -> Resource:
    source_credentials, _ = google.auth.default(scopes=SCOPE)
    target_credentials = impersonated_credentials.Credentials(
        source_credentials=source_credentials,
        target_principal=SERVICE_ACCOUNT_EMAIL,
        target_scopes=SCOPE,
        lifetime=3600,
    )
    service = build(API_SERVICE_NAME, API_VERSION, credentials=target_credentials)
    return service


def score(metrics):
    domain, is_up, word_count, search_clicks, search_imp, search_pos, ctr, gtag, users = metrics
    s = (
        int(users.isdigit() and users or 0) * 10000
        + int(search_clicks) * 100
        + int(search_imp) * 10
        + int(word_count)
    )
    return *metrics, str(s / 10000)


if __name__ == "__main__":
    DOMAINS = [
        "rossfenning.co.uk",
        "avengerpenguin.com",
        "traditionalmead.uk",
        "codesnips.pro",
        "historyofsound.com",
        "wonkypaedia.org",
    ]

    table = Table(title="Websites")
    table.add_column("Domain")
    table.add_column("Up")
    table.add_column("Words")
    table.add_column("Clicks")
    table.add_column("Impressions")
    table.add_column("Position")
    table.add_column("Min CTR")
    table.add_column("Google Tag ID")
    table.add_column("Users 28d")
    table.add_column("Score")

    for d in DOMAINS:
        table.add_row(*score(make_row(d)))

    console = Console()
    console.print(table)
