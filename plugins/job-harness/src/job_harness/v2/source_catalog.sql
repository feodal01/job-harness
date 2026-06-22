PRAGMA foreign_keys = ON;

CREATE TABLE sources (
    sort_order INTEGER NOT NULL UNIQUE CHECK (sort_order >= 0),
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (source_type IN ('aggregator', 'company_career')),
    transport TEXT NOT NULL CHECK (transport IN ('http', 'browser', 'hybrid')),
    source_limit INTEGER NOT NULL CHECK (source_limit > 0)
);

CREATE TABLE countries (
    country_code TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    search_enabled INTEGER NOT NULL CHECK (search_enabled IN (0, 1))
);

CREATE TABLE source_countries (
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    country_order INTEGER NOT NULL CHECK (country_order >= 0),
    country TEXT NOT NULL REFERENCES countries(country_code),
    PRIMARY KEY (source_id, country),
    UNIQUE (source_id, country_order)
);

CREATE TABLE source_criteria (
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    criterion_order INTEGER NOT NULL CHECK (criterion_order >= 0),
    criterion TEXT NOT NULL CHECK (
        criterion IN (
            'query',
            'grades',
            'salary_from',
            'published_since',
            'relocation',
            'remote_in_country',
            'remote_global',
            'countries',
            'cities'
        )
    ),
    capability TEXT NOT NULL CHECK (
        capability IN ('native_request', 'structured_output', 'unsupported')
    ),
    PRIMARY KEY (source_id, criterion),
    UNIQUE (source_id, criterion_order)
);

CREATE TABLE source_required_fixture_kinds (
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (
        kind IN (
            'no_results',
            'pagination',
            'detail',
            'optional_fields',
            'blocked',
            'rate_limited',
            'login',
            'geo_blocked',
            'malformed_source'
        )
    ),
    PRIMARY KEY (source_id, kind)
);

CREATE TABLE parser_fixtures (
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    fixture_order INTEGER NOT NULL CHECK (fixture_order >= 0),
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (
        kind IN (
            'success_non_empty',
            'no_results',
            'pagination',
            'detail',
            'optional_fields',
            'blocked',
            'rate_limited',
            'login',
            'geo_blocked',
            'malformed_source'
        )
    ),
    captured_artifact_path TEXT NOT NULL,
    metadata_path TEXT NOT NULL,
    golden_path TEXT NOT NULL,
    real_capture INTEGER NOT NULL CHECK (real_capture IN (0, 1)),
    golden_reviewed_by TEXT NOT NULL,
    PRIMARY KEY (source_id, name),
    UNIQUE (source_id, fixture_order)
);

INSERT INTO sources (sort_order, source_id, source_type, transport, source_limit)
VALUES
    (0, 'habr_career', 'aggregator', 'http', 50),
    (1, 'hh_ru', 'aggregator', 'http', 100),
    (2, 'talanto', 'aggregator', 'http', 50),
    (3, 'career:vk', 'company_career', 'http', 25),
    (4, 'career:jetbrains', 'company_career', 'http', 120),
    (5, 'geekjob', 'aggregator', 'http', 50);

INSERT INTO countries (country_code, display_name, search_enabled)
VALUES
    ('RU', 'Russia', 1);

INSERT INTO source_countries (source_id, country_order, country)
VALUES
    ('habr_career', 0, 'RU'),
    ('hh_ru', 0, 'RU'),
    ('career:vk', 0, 'RU');

INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
VALUES
    ('habr_career', 0, 'query', 'native_request'),
    ('habr_career', 1, 'grades', 'native_request'),
    ('habr_career', 2, 'salary_from', 'native_request'),
    ('habr_career', 3, 'published_since', 'structured_output'),
    ('habr_career', 4, 'relocation', 'unsupported'),
    ('habr_career', 5, 'remote_in_country', 'structured_output'),
    ('habr_career', 6, 'remote_global', 'unsupported'),
    ('habr_career', 7, 'countries', 'structured_output'),
    ('habr_career', 8, 'cities', 'structured_output'),
    ('hh_ru', 0, 'query', 'native_request'),
    ('hh_ru', 1, 'grades', 'structured_output'),
    ('hh_ru', 2, 'salary_from', 'native_request'),
    ('hh_ru', 3, 'published_since', 'structured_output'),
    ('hh_ru', 4, 'relocation', 'unsupported'),
    ('hh_ru', 5, 'remote_in_country', 'structured_output'),
    ('hh_ru', 6, 'remote_global', 'unsupported'),
    ('hh_ru', 7, 'countries', 'structured_output'),
    ('hh_ru', 8, 'cities', 'structured_output'),
    ('talanto', 0, 'query', 'native_request'),
    ('talanto', 1, 'grades', 'structured_output'),
    ('talanto', 2, 'salary_from', 'structured_output'),
    ('talanto', 3, 'published_since', 'structured_output'),
    ('talanto', 4, 'relocation', 'unsupported'),
    ('talanto', 5, 'remote_in_country', 'structured_output'),
    ('talanto', 6, 'remote_global', 'unsupported'),
    ('talanto', 7, 'countries', 'structured_output'),
    ('talanto', 8, 'cities', 'structured_output'),
    ('career:vk', 0, 'query', 'native_request'),
    ('career:vk', 1, 'grades', 'unsupported'),
    ('career:vk', 2, 'salary_from', 'unsupported'),
    ('career:vk', 3, 'published_since', 'unsupported'),
    ('career:vk', 4, 'relocation', 'unsupported'),
    ('career:vk', 5, 'remote_in_country', 'structured_output'),
    ('career:vk', 6, 'remote_global', 'unsupported'),
    ('career:vk', 7, 'countries', 'structured_output'),
    ('career:vk', 8, 'cities', 'structured_output'),
    ('career:jetbrains', 0, 'query', 'structured_output'),
    ('career:jetbrains', 1, 'grades', 'unsupported'),
    ('career:jetbrains', 2, 'salary_from', 'unsupported'),
    ('career:jetbrains', 3, 'published_since', 'structured_output'),
    ('career:jetbrains', 4, 'relocation', 'unsupported'),
    ('career:jetbrains', 5, 'remote_in_country', 'structured_output'),
    ('career:jetbrains', 6, 'remote_global', 'structured_output'),
    ('career:jetbrains', 7, 'countries', 'structured_output'),
    ('career:jetbrains', 8, 'cities', 'structured_output'),
    ('geekjob', 0, 'query', 'structured_output'),
    ('geekjob', 1, 'grades', 'unsupported'),
    ('geekjob', 2, 'salary_from', 'structured_output'),
    ('geekjob', 3, 'published_since', 'structured_output'),
    ('geekjob', 4, 'relocation', 'unsupported'),
    ('geekjob', 5, 'remote_in_country', 'structured_output'),
    ('geekjob', 6, 'remote_global', 'structured_output'),
    ('geekjob', 7, 'countries', 'structured_output'),
    ('geekjob', 8, 'cities', 'unsupported');

INSERT INTO source_required_fixture_kinds (source_id, kind)
VALUES
    ('habr_career', 'no_results'),
    ('habr_career', 'pagination'),
    ('habr_career', 'detail'),
    ('habr_career', 'optional_fields'),
    ('hh_ru', 'no_results'),
    ('hh_ru', 'pagination'),
    ('hh_ru', 'optional_fields'),
    ('talanto', 'no_results'),
    ('career:vk', 'no_results'),
    ('geekjob', 'no_results');

INSERT INTO parser_fixtures (
    source_id,
    fixture_order,
    name,
    kind,
    captured_artifact_path,
    metadata_path,
    golden_path,
    real_capture,
    golden_reviewed_by
)
VALUES
    (
        'habr_career',
        0,
        'habr_career-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/habr_career/success/response.html',
        'tests/v2/fixtures/scrapers/habr_career/success/meta.json',
        'tests/v2/fixtures/scrapers/habr_career/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'habr_career',
        1,
        'habr_career-no_results',
        'no_results',
        'tests/v2/fixtures/scrapers/habr_career/no_results/response.html',
        'tests/v2/fixtures/scrapers/habr_career/no_results/meta.json',
        'tests/v2/fixtures/scrapers/habr_career/no_results/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'habr_career',
        2,
        'habr_career-pagination',
        'pagination',
        'tests/v2/fixtures/scrapers/habr_career/pagination/response.html',
        'tests/v2/fixtures/scrapers/habr_career/pagination/meta.json',
        'tests/v2/fixtures/scrapers/habr_career/pagination/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'habr_career',
        3,
        'habr_career-detail',
        'detail',
        'tests/v2/fixtures/scrapers/habr_career/detail/response.html',
        'tests/v2/fixtures/scrapers/habr_career/detail/meta.json',
        'tests/v2/fixtures/scrapers/habr_career/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'habr_career',
        4,
        'habr_career-optional_fields',
        'optional_fields',
        'tests/v2/fixtures/scrapers/habr_career/success/response.html',
        'tests/v2/fixtures/scrapers/habr_career/optional_fields/meta.json',
        'tests/v2/fixtures/scrapers/habr_career/optional_fields/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'hh_ru',
        0,
        'hh_ru-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/hh_ru/success/response.html',
        'tests/v2/fixtures/scrapers/hh_ru/success/meta.json',
        'tests/v2/fixtures/scrapers/hh_ru/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'hh_ru',
        1,
        'hh_ru-no_results',
        'no_results',
        'tests/v2/fixtures/scrapers/hh_ru/no_results/response.html',
        'tests/v2/fixtures/scrapers/hh_ru/no_results/meta.json',
        'tests/v2/fixtures/scrapers/hh_ru/no_results/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'hh_ru',
        2,
        'hh_ru-pagination',
        'pagination',
        'tests/v2/fixtures/scrapers/hh_ru/pagination/response.html',
        'tests/v2/fixtures/scrapers/hh_ru/pagination/meta.json',
        'tests/v2/fixtures/scrapers/hh_ru/pagination/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'hh_ru',
        3,
        'hh_ru-optional_fields',
        'optional_fields',
        'tests/v2/fixtures/scrapers/hh_ru/success/response.html',
        'tests/v2/fixtures/scrapers/hh_ru/optional_fields/meta.json',
        'tests/v2/fixtures/scrapers/hh_ru/optional_fields/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:vk',
        0,
        'career:vk-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_vk/success/response.html',
        'tests/v2/fixtures/scrapers/career_vk/success/meta.json',
        'tests/v2/fixtures/scrapers/career_vk/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:vk',
        1,
        'career:vk-no_results',
        'no_results',
        'tests/v2/fixtures/scrapers/career_vk/no_results/response.html',
        'tests/v2/fixtures/scrapers/career_vk/no_results/meta.json',
        'tests/v2/fixtures/scrapers/career_vk/no_results/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'talanto',
        0,
        'talanto-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/talanto/success/response.html',
        'tests/v2/fixtures/scrapers/talanto/success/meta.json',
        'tests/v2/fixtures/scrapers/talanto/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'talanto',
        1,
        'talanto-no_results',
        'no_results',
        'tests/v2/fixtures/scrapers/talanto/no_results/response.html',
        'tests/v2/fixtures/scrapers/talanto/no_results/meta.json',
        'tests/v2/fixtures/scrapers/talanto/no_results/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:jetbrains',
        0,
        'career:jetbrains-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_jetbrains/success/response.json',
        'tests/v2/fixtures/scrapers/career_jetbrains/success/meta.json',
        'tests/v2/fixtures/scrapers/career_jetbrains/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'geekjob',
        0,
        'geekjob-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/geekjob/success/response.html',
        'tests/v2/fixtures/scrapers/geekjob/success/meta.json',
        'tests/v2/fixtures/scrapers/geekjob/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'geekjob',
        1,
        'geekjob-no_results',
        'no_results',
        'tests/v2/fixtures/scrapers/geekjob/no_results/response.html',
        'tests/v2/fixtures/scrapers/geekjob/no_results/meta.json',
        'tests/v2/fixtures/scrapers/geekjob/no_results/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    );
