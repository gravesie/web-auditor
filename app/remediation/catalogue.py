"""The remediation catalogue: every check mapped to impact, effort, why and how.

Entries are keyed by the composite "{audit_key}.{check_key}", which is globally
unique even though a bare check key is only unique within its audit. A handful of
checks are generated per item (one per missing security header, one per detected
component); those resolve through FAMILIES by key prefix when there is no exact
entry.

This is curated content authored by us, not data from end users. It lives in code
for now (type-checked, testable, navigable); it can move to a database when the
product goes multi-user. The coverage test asserts every check the audits can emit
resolves to an entry, so a new check cannot ship without its remediation.
"""

from __future__ import annotations

from app.remediation.schema import RemediationEntry

# Short alias to keep the catalogue entries compact.
R = RemediationEntry

# Exact entries, keyed by "{audit_key}.{check_key}".
CATALOGUE: dict[str, RemediationEntry] = {
    # --- build_security ---
    "build_security.ai_build_likelihood": R(
        1, 2,
        "A site that reads as a generic AI-built template gives buyers little reason to trust it.",
        "Replace boilerplate copy with specific, first-hand detail about your business.",
    ),
    "build_security.cookie_flags": R(
        2, 1,
        "Cookies without Secure and HttpOnly flags can be intercepted or read by scripts.",
        "Set Secure, HttpOnly and SameSite on session cookies.",
    ),
    "build_security.copyright_year": R(
        1, 1,
        "An out-of-date copyright year signals an unmaintained site to visitors.",
        "Update the footer year, ideally from the server date so it never goes stale.",
    ),
    "build_security.crawl_signals": R(
        2, 2,
        "Broken links and console errors frustrate visitors and waste crawl budget.",
        "Fix broken internal links and clear the JavaScript console errors.",
    ),
    "build_security.exposed_paths": R(
        5, 1,
        "A public .git or .env file can leak source code, credentials and API keys.",
        "Block these paths at the server and rotate any exposed secrets at once.",
    ),
    "build_security.https_enforced": R(
        4, 1,
        "Serving over HTTP exposes visitors to interception and warns them off in the browser.",
        "Redirect all HTTP traffic to HTTPS with a 301.",
    ),
    "build_security.info_leakage": R(
        2, 1,
        "Verbose server headers hand attackers a map of what to try.",
        "Strip or generalise Server and X-Powered-By headers.",
    ),
    "build_security.known_vulnerabilities": R(
        4, 3,
        "Running end-of-life software leaves known security holes unpatched.",
        "Upgrade to a supported version on a maintained release line.",
    ),
    "build_security.mixed_content": R(
        3, 2,
        "Mixed content breaks the padlock and can be blocked outright by browsers.",
        "Load every asset over HTTPS.",
    ),
    "build_security.stack_detected": R(
        1, 1,
        "Knowing the platform is useful context for the other fixes; no action in itself.",
        "No change needed; this is informational.",
    ),
    "build_security.tls_expiry": R(
        4, 1,
        "An expired certificate makes the site unreachable and alarms visitors.",
        "Renew the certificate and turn on auto-renewal.",
    ),
    "build_security.tls_valid": R(
        5, 1,
        "An invalid certificate blocks visitors behind a full-page security warning.",
        "Install a valid certificate from a trusted CA that covers this domain.",
    ),
    "build_security.version_disclosure": R(
        2, 1,
        "Exposed version numbers tell attackers exactly which exploits to try.",
        "Hide version strings from response headers and generator meta tags.",
    ),
    "build_security.header_strict_transport_security": R(
        3, 1,
        "Without HSTS a first visit can be downgraded to HTTP and intercepted.",
        "Add a Strict-Transport-Security header with a long max-age.",
    ),
    "build_security.header_content_security_policy": R(
        3, 2,
        "No Content-Security-Policy leaves the site open to cross-site scripting and injection.",
        "Add a Content-Security-Policy that allows only the sources you trust.",
    ),
    "build_security.header_x_frame_options": R(
        2, 1,
        "Without X-Frame-Options the site can be framed for clickjacking.",
        "Set X-Frame-Options to SAMEORIGIN, or use CSP frame-ancestors.",
    ),
    "build_security.header_x_content_type_options": R(
        2, 1,
        "Without this header browsers may MIME-sniff and misread files.",
        "Set X-Content-Type-Options to nosniff.",
    ),
    "build_security.header_referrer_policy": R(
        1, 1,
        "A loose referrer policy leaks the pages visitors came from to third parties.",
        "Set Referrer-Policy to strict-origin-when-cross-origin.",
    ),
    "build_security.header_permissions_policy": R(
        1, 1,
        "Without a permissions policy, embedded scripts can ask for camera, mic and location.",
        "Add a Permissions-Policy that disables the features you don't use.",
    ),
    # --- compliance ---
    "compliance.accessibility_statement": R(
        1, 1,
        "An accessibility statement is expected of compliant UK sites and signals good faith.",
        "Publish a statement covering your conformance level and a contact route.",
    ),
    "compliance.company_identity": R(
        2, 1,
        "UK law requires trading and company details to be shown; their absence hurts trust.",
        "Add company name, number and registered address to the footer or a legal page.",
    ),
    "compliance.consent_mechanism": R(
        4, 2,
        "Setting non-essential cookies without a consent mechanism breaches UK GDPR and PECR.",
        "Install a consent banner that blocks non-essential tags until the visitor agrees.",
    ),
    "compliance.form_privacy_notice": R(
        2, 1,
        "Collecting personal data with no notice at the point of collection breaches UK GDPR.",
        "Add a short privacy notice and link beside each form.",
    ),
    "compliance.form_secure_submission": R(
        4, 2,
        "A form that submits over HTTP exposes personal data in transit.",
        "Serve and submit every form over HTTPS.",
    ),
    "compliance.pii_forms": R(
        2, 1,
        "Forms that gather personal data carry data-protection duties that are easy to miss.",
        "Map what each form collects and confirm a lawful basis and notice for it.",
    ),
    "compliance.privacy_policy_completeness": R(
        3, 2,
        "A privacy policy missing required disclosures fails UK GDPR transparency rules.",
        "Cover what you collect, why, how long, who with, and the visitor's rights.",
    ),
    "compliance.privacy_policy_present": R(
        4, 2,
        "Operating with no privacy policy breaches UK GDPR transparency duties.",
        "Publish a privacy policy linked from every page.",
    ),
    "compliance.secure_transport": R(
        4, 1,
        "Handling personal data without HTTPS breaches the security principle of UK GDPR.",
        "Move the whole site to HTTPS.",
    ),
    "compliance.terms_present": R(
        1, 1,
        "Without terms of use you have no stated basis for how visitors may use the site.",
        "Publish terms of use and link them in the footer.",
    ),
    "compliance.third_party_inventory": R(
        2, 2,
        "Each third-party tag may move personal data, sometimes overseas, and must be disclosed.",
        "List your processors and check each has a lawful transfer route.",
    ),
    "compliance.trackers_before_consent": R(
        5, 2,
        "Loading trackers before consent is the most common and most enforced UK GDPR breach.",
        "Hold all non-essential tags until the visitor consents, through your consent banner.",
    ),
    "compliance.us_processor_transfer": R(
        2, 2,
        "Sending personal data to US processors needs a valid transfer mechanism.",
        "Confirm each US processor is covered by the Data Privacy Framework or SCCs.",
    ),
    "compliance.wcag_automated": R(
        3, 2,
        "Accessibility failures shut out disabled visitors and carry legal risk.",
        "Fix the flagged contrast, labelling and structure issues, then retest.",
    ),
    # --- content_quality ---
    "content_quality.authorship": R(
        2, 2,
        "Content with no named author is harder for readers and search engines to trust.",
        "Add author bylines with a short credential line.",
    ),
    "content_quality.content_dates": R(
        2, 1,
        "Undated content looks stale and search engines can't judge its freshness.",
        "Show a published and updated date on articles.",
    ),
    "content_quality.copyright_current": R(
        1, 1,
        "An old copyright year suggests the site is abandoned.",
        "Update the footer year automatically from the server date.",
    ),
    "content_quality.depth_analysis": R(
        3, 3,
        "Shallow pages rarely rank or convince a buyer to act.",
        "Expand key pages to fully answer the visitor's question with specifics.",
    ),
    "content_quality.duplicate_content": R(
        3, 2,
        "Duplicate pages split ranking signals and can suppress all copies.",
        "Consolidate duplicates and canonical them to the preferred URL.",
    ),
    "content_quality.first_hand_experience": R(
        3, 3,
        "Content showing no real experience reads as generic and loses to rivals who show theirs.",
        "Add examples, results and detail only someone who's done the work would know.",
    ),
    "content_quality.generic_prose": R(
        2, 2,
        "Generic, padded copy gives readers nothing specific to act on.",
        "Replace filler with concrete facts, numbers and examples.",
    ),
    "content_quality.physical_presence": R(
        2, 1,
        "A visible address reassures buyers and supports local trust.",
        "Show your business address and contact details.",
    ),
    "content_quality.scannability": R(
        2, 2,
        "Walls of text get skipped; few visitors read past a dense screen.",
        "Break copy into short sections with headings and lists.",
    ),
    "content_quality.social_proof": R(
        3, 2,
        "Without testimonials or logos, buyers have no outside reason to believe you.",
        "Add named testimonials, case results or client logos.",
    ),
    "content_quality.substance": R(
        3, 3,
        "Pages light on substance neither rank nor convert.",
        "Add the depth a buyer needs to make a decision.",
    ),
    "content_quality.trust_pages": R(
        3, 1,
        "Missing About and Contact pages make a business look untrustworthy.",
        "Publish clear About and Contact pages.",
    ),
    # --- geo ---
    "geo.ai_answer_citation": R(
        4, 3,
        "If AI assistants don't cite you, you're invisible to a fast-growing share of research.",
        "Build clear, sourced, quotable answers to the questions buyers ask AI.",
    ),
    "geo.ai_crawler_access": R(
        4, 1,
        "Blocking AI crawlers in robots.txt removes you from AI answers entirely.",
        "Allow reputable AI crawlers in robots.txt unless you have a clear reason not to.",
    ),
    "geo.corroboration_signals": R(
        2, 2,
        "Claims with no sources or data are easy for AI and readers to discount.",
        "Back key claims with data, sources or named evidence.",
    ),
    "geo.extractable_chunks": R(
        3, 2,
        "AI engines lift self-contained passages; sprawling copy gets passed over.",
        "Write tight, standalone paragraphs that each answer one question.",
    ),
    "geo.faq_content": R(
        2, 2,
        "FAQs match how people and AI ask questions, and feed answer engines directly.",
        "Add a genuine FAQ answering the questions buyers actually ask.",
    ),
    "geo.icp_question_coverage": R(
        4, 3,
        "If the site doesn't answer your buyers' real questions, search and AI won't send them.",
        "Map your buyers' questions and write a clear answer to each.",
    ),
    "geo.icp_question_source": R(
        2, 2,
        "Without your real buyer questions, coverage scoring is guesswork.",
        "Connect Search Console, or supply the questions buyers ask you.",
    ),
    "geo.llms_txt": R(
        1, 1,
        "An llms.txt file guides AI engines to your best content, and few rivals have one yet.",
        "Publish an llms.txt pointing to your key pages.",
    ),
    "geo.off_page_authority": R(
        3, 3,
        "Without citations and links from other sites, both search and AI rank you lower.",
        "Earn mentions and links from sites your buyers already trust.",
    ),
    "geo.qa_structure": R(
        2, 2,
        "Question-and-answer structure is what answer engines extract most readily.",
        "Format key content as clear questions with direct answers.",
    ),
    # --- messaging ---
    "messaging.audience_signal": R(
        3, 2,
        "If visitors can't tell the site is for them, they leave.",
        "Name your audience and their problem in the first screen.",
    ),
    "messaging.benefit_language": R(
        3, 2,
        "Feature lists don't sell; buyers act on what they get.",
        "Rewrite key copy around the outcomes the buyer cares about.",
    ),
    "messaging.brand_consistency": R(
        2, 2,
        "Inconsistent naming and tone make a business look disjointed.",
        "Settle on one name, tagline and tone, and apply it throughout.",
    ),
    "messaging.clarity_judgement": R(
        4, 2,
        "If a visitor can't tell what you do in five seconds, you lose them before the pitch.",
        "Make the homepage say plainly what you do, for whom, and the next step.",
    ),
    "messaging.conversion_systems": R(
        4, 2,
        "Traffic with no clear way to convert is wasted spend.",
        "Add an obvious primary action (enquiry, call or booking) on every key page.",
    ),
    "messaging.cta_present": R(
        4, 1,
        "Without a clear call to action, interested visitors don't know what to do next.",
        "Add one clear, repeated call to action.",
    ),
    "messaging.hero_headline": R(
        4, 1,
        "The hero headline is the first thing read; a weak one loses the visitor.",
        "Lead with a headline that states the value in plain words.",
    ),
    "messaging.icp_fit": R(
        3, 3,
        "Messaging aimed at no one in particular converts no one.",
        "Sharpen the offer around your best-fit customer.",
    ),
    "messaging.plain_language": R(
        2, 2,
        "Jargon makes buyers work to understand you, so they don't.",
        "Replace jargon with the plain words your buyer uses.",
    ),
    "messaging.proof": R(
        3, 2,
        "Claims without proof don't move buyers.",
        "Add evidence: results, testimonials, named clients or data.",
    ),
    # --- on_page_seo ---
    "on_page_seo.anticipated_phrases": R(
        2, 2,
        "Without rank data, the target phrases shown are estimates.",
        "Connect Search Console to see the phrases you actually rank for.",
    ),
    "on_page_seo.average_position": R(
        3, 3,
        "A low average position means few clicks even when impressions are high.",
        "Improve the pages closest to page one first.",
    ),
    "on_page_seo.content_depth": R(
        3, 3,
        "Thin pages struggle to rank against fuller competitors.",
        "Expand pages to fully cover the topic.",
    ),
    "on_page_seo.h1": R(
        3, 1,
        "A missing or duplicate H1 muddies what the page is about for search engines.",
        "Give each page one clear, descriptive H1.",
    ),
    "on_page_seo.image_alt": R(
        2, 1,
        "Missing alt text loses image-search traffic and fails accessibility.",
        "Add descriptive alt text to meaningful images.",
    ),
    "on_page_seo.intent_analysis": R(
        3, 3,
        "Content that misreads search intent won't rank however good it is.",
        "Match each page to what the searcher actually wants.",
    ),
    "on_page_seo.local_signals": R(
        3, 2,
        "Without local signals you miss nearby buyers searching with intent.",
        "Add location pages, NAP details and a Google Business Profile link.",
    ),
    "on_page_seo.meta_description": R(
        2, 1,
        "A weak meta description lowers click-through from the search results.",
        "Write a compelling meta description under 160 characters for each key page.",
    ),
    "on_page_seo.nap_presence": R(
        2, 1,
        "Inconsistent name, address and phone hurts local ranking and trust.",
        "Show consistent NAP details site-wide.",
    ),
    "on_page_seo.rankings": R(
        3, 3,
        "Without rank data you're optimising blind.",
        "Connect Search Console to track real rankings.",
    ),
    "on_page_seo.search_visibility": R(
        4, 3,
        "Low search visibility means few buyers ever find you.",
        "Target the queries your buyers use and improve the matching pages.",
    ),
    "on_page_seo.semantic_coverage": R(
        3, 3,
        "Covering a topic shallowly cedes ranking to fuller rivals.",
        "Cover the related subtopics a complete answer needs.",
    ),
    "on_page_seo.striking_distance": R(
        5, 2,
        "Keywords ranking just off page one are the fastest traffic wins you have.",
        "Strengthen the pages ranking 11 to 20 to push them onto page one.",
    ),
    "on_page_seo.title_h1_alignment": R(
        2, 1,
        "A title and H1 that disagree weaken the page's topic signal.",
        "Align the title tag and H1 on the page's main keyword.",
    ),
    "on_page_seo.title_tag": R(
        4, 1,
        "The title tag is the strongest on-page ranking signal and your headline in search.",
        "Write a unique, keyword-led title under 60 characters for each page.",
    ),
    "on_page_seo.title_uniqueness": R(
        3, 2,
        "Duplicate titles make pages compete with each other in search.",
        "Give every page a distinct title.",
    ),
    # --- performance ---
    "performance.compression": R(
        2, 1,
        "Uncompressed text makes pages slower to load.",
        "Enable Gzip or Brotli compression on the server.",
    ),
    "performance.lighthouse_score": R(
        3, 3,
        "A slow site loses visitors and ranks lower, especially on mobile.",
        "Work through the Lighthouse opportunities, biggest first.",
    ),
    "performance.page_weight": R(
        3, 2,
        "Heavy pages load slowly on mobile data, driving visitors away.",
        "Compress images, defer scripts and trim unused code.",
    ),
    "performance.pagespeed_unavailable": R(
        1, 1,
        "Without PageSpeed data, performance can't be measured directly.",
        "Let the PageSpeed check run, or add an API key for reliable results.",
    ),
    "performance.ttfb": R(
        3, 2,
        "A slow first byte delays everything else on the page.",
        "Improve server response with caching or better hosting.",
    ),
    "performance.cwv_lcp": R(
        4, 2,
        "A slow LCP means the main content appears late, and Google marks the page down for it.",
        "Optimise the hero image and server response so the main content loads sooner.",
    ),
    "performance.cwv_inp": R(
        3, 2,
        "Poor Interaction to Next Paint makes the site feel laggy when users tap or click.",
        "Reduce heavy JavaScript so the page responds quickly to input.",
    ),
    "performance.cwv_cls": R(
        3, 2,
        "Layout shift moves content as it loads, causing mis-taps and frustration.",
        "Set size attributes on images and reserve space for late-loading elements.",
    ),
    # --- schema ---
    "schema.core_types": R(
        3, 2,
        "Without the right schema types you miss rich results that lift click-through.",
        "Add Organization and the page-appropriate types as JSON-LD.",
    ),
    "schema.deprecated_rich": R(
        1, 1,
        "Deprecated schema types no longer earn rich results and just add clutter.",
        "Remove FAQ and HowTo markup Google no longer rewards, or keep only for clarity.",
    ),
    "schema.json_ld_valid": R(
        3, 1,
        "Invalid structured data is ignored, so you lose the rich results it would earn.",
        "Fix the JSON-LD errors and confirm with the Rich Results Test.",
    ),
    "schema.organization_entity": R(
        3, 2,
        "Without an Organization entity, search engines can't tie your brand together.",
        "Add Organization schema with name, logo and social profiles.",
    ),
    "schema.required_properties": R(
        2, 1,
        "Schema missing required properties won't qualify for rich results.",
        "Fill the required properties for each type you use.",
    ),
    "schema.rich_eligible_types": R(
        2, 2,
        "Pages eligible for rich results that aren't marked up miss free visibility.",
        "Add the supported schema for products, articles or reviews where they fit.",
    ),
    "schema.structured_data_present": R(
        3, 2,
        "With no structured data you forgo rich results and clearer search understanding.",
        "Add JSON-LD structured data, starting with Organization.",
    ),
    # --- technical_seo ---
    "technical_seo.canonical_tag": R(
        3, 1,
        "Without correct canonicals, duplicate URLs split ranking signals.",
        "Set a self-referencing canonical on each page.",
    ),
    "technical_seo.crawl_depth": R(
        2, 2,
        "Pages buried many clicks deep are crawled and ranked less.",
        "Flatten the structure so key pages are within three clicks.",
    ),
    "technical_seo.homepage_indexable": R(
        5, 1,
        "A non-indexable homepage can drop your whole site from search.",
        "Remove the noindex or robots block from the homepage.",
    ),
    "technical_seo.hreflang": R(
        2, 2,
        "Broken hreflang serves the wrong language version to searchers.",
        "Pair each language URL correctly in hreflang, with return tags.",
    ),
    "technical_seo.https_canonicalisation": R(
        3, 1,
        "Serving the site on several URL variants splits ranking signals.",
        "Redirect all variants to one canonical HTTPS host.",
    ),
    "technical_seo.indexed_count": R(
        4, 3,
        "Pages that aren't indexed can't rank or bring traffic.",
        "Find why pages aren't indexed, fix the blocks, then request indexing.",
    ),
    "technical_seo.js_content_dependency": R(
        3, 3,
        "Content that only appears with JavaScript may not be indexed reliably.",
        "Server-render or pre-render key content so it's in the initial HTML.",
    ),
    "technical_seo.robots_txt": R(
        4, 1,
        "A misconfigured robots.txt can block search engines from the whole site.",
        "Allow crawling of important pages and link your sitemap.",
    ),
    "technical_seo.sitemap_coverage": R(
        2, 1,
        "Pages left out of the sitemap are found and indexed more slowly.",
        "List all live, canonical pages in the XML sitemap.",
    ),
    "technical_seo.sitemap_health": R(
        2, 1,
        "A sitemap full of dead or redirecting URLs wastes crawl budget and signals neglect.",
        "Keep the sitemap to live, canonical, 200-status URLs.",
    ),
    "technical_seo.status_health": R(
        3, 2,
        "Broken and redirecting pages waste crawl budget and lose link value.",
        "Fix 4xx and 5xx pages and reduce redirect chains.",
    ),
    "technical_seo.viewport_meta": R(
        3, 1,
        "Without a viewport tag the site renders badly on mobile, where most traffic is.",
        "Add a responsive viewport meta tag.",
    ),
    "technical_seo.xml_sitemap": R(
        3, 1,
        "Without a sitemap, search engines find your pages more slowly.",
        "Publish an XML sitemap and submit it in Search Console.",
    ),
}

# Prefix families for checks generated per item, where one remediation fits the whole
# family. Longest matching prefix wins. Used only when there is no exact entry.
FAMILIES: dict[str, RemediationEntry] = {
    "build_security.component_": R(
        4, 3,
        "An end-of-life component no longer receives security patches.",
        "Upgrade the component to a supported release.",
    ),
}


def lookup(audit_key: str, check_key: str) -> RemediationEntry | None:
    """Resolve a check to its remediation: exact match first, then prefix family."""
    full = f"{audit_key}.{check_key}"
    exact = CATALOGUE.get(full)
    if exact is not None:
        return exact
    for prefix in sorted(FAMILIES, key=len, reverse=True):
        if full.startswith(prefix):
            return FAMILIES[prefix]
    return None
