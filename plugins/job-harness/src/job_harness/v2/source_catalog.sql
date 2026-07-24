PRAGMA foreign_keys = ON;

CREATE TABLE sources (
    sort_order INTEGER NOT NULL UNIQUE CHECK (sort_order >= 0),
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (source_type IN ('aggregator', 'company_career')),
    transport TEXT NOT NULL CHECK (transport IN ('http', 'browser', 'hybrid')),
    source_limit INTEGER NOT NULL CHECK (source_limit > 0),
    identity_namespace TEXT,
    listing_parser_id TEXT NOT NULL,
    listing_parser_version TEXT NOT NULL
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
            'compensation',
            'published_since',
            'relocation',
            'work_formats',
            'remote_scopes',
            'vacancy_geographies',
            'employer_geographies'
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

INSERT INTO sources (
    sort_order, source_id, source_type, transport, source_limit,
    listing_parser_id, listing_parser_version
)
VALUES
    (0, 'habr_career', 'aggregator', 'http', 50, 'habr_career.search', '1.0'),
    (1, 'hh_ru', 'aggregator', 'http', 100, 'hh_ru.search', '1.0'),
    (2, 'talanto', 'aggregator', 'http', 50, 'talanto.search', '1.0'),
    (3, 'career:vk', 'company_career', 'http', 300, 'career:vk.search', '1.0'),
    (4, 'career:jetbrains', 'company_career', 'http', 120, 'career:jetbrains.search', '1.0'),
    (5, 'geekjob', 'aggregator', 'http', 50, 'geekjob.search', '1.0'),
    (6, 'talento', 'aggregator', 'http', 50, 'talento.search', '1.0'),
    (7, 'finder_work', 'aggregator', 'http', 100, 'finder_work.search', '1.0'),
    (8, 'getmatch', 'aggregator', 'http', 100, 'getmatch.search', '1.0'),
    (9, 'it_jobs_uz', 'aggregator', 'http', 100, 'it_jobs_uz.search', '1.0'),
    (10, 'hirify', 'aggregator', 'http', 100, 'hirify.search', '1.0'),
    (11, 'jobturbo', 'aggregator', 'http', 50, 'jobturbo.search', '1.0'),
    (12, 'hirehi', 'aggregator', 'http', 50, 'hirehi.search', '1.0'),
    (13, 'staff_am', 'aggregator', 'http', 100, 'staff_am.search', '1.0'),
    (14, 'career:ibs', 'company_career', 'http', 100, 'career:ibs.search', '1.0'),
    (15, 'career:amocrm', 'company_career', 'http', 50, 'career:amocrm.search', '1.0'),
    (16, 'career:coinspaid', 'company_career', 'http', 100, 'career:coinspaid.search', '1.0'),
    (17, 'career:appfollow', 'company_career', 'http', 20, 'career:appfollow.search', '1.0'),
    (18, 'career:airslate', 'company_career', 'http', 100, 'career:airslate.search', '1.0'),
    (19, 'career:wintermute', 'company_career', 'http', 100, 'career:wintermute.search', '1.0'),
    (20, 'career:truv', 'company_career', 'http', 100, 'career:truv.search', '1.0'),
    (21, 'career:termius', 'company_career', 'http', 100, 'career:termius.search', '1.0'),
    (22, 'career:outschool', 'company_career', 'http', 100, 'career:outschool.search', '1.0'),
    (23, 'career:zeroavia', 'company_career', 'http', 100, 'career:zeroavia.search', '1.0'),
    (24, 'career:wallarm', 'company_career', 'http', 100, 'career:wallarm.search', '1.0'),
    (25, 'career:chainstack', 'company_career', 'http', 100, 'career:chainstack.search', '1.0'),
    (26, 'career:3commas', 'company_career', 'http', 100, 'career:3commas.search', '1.0'),
    (27, 'career:collectly', 'company_career', 'http', 100, 'career:collectly.search', '1.0'),
    (28, 'career:planner5d', 'company_career', 'http', 100, 'career:planner5d.search', '1.0'),
    (29, 'career:superannotate', 'company_career', 'http', 100, 'career:superannotate.search', '1.0'),
    (30, 'career:xsolla', 'company_career', 'http', 200, 'career:xsolla.search', '1.0'),
    (31, 'career:clickhouse', 'company_career', 'http', 200, 'career:clickhouse.search', '1.0'),
    (32, 'career:datafold', 'company_career', 'http', 100, 'career:datafold.search', '1.0'),
    (33, 'career:inworld', 'company_career', 'http', 100, 'career:inworld.search', '1.0'),
    (34, 'career:luminai', 'company_career', 'http', 100, 'career:luminai.search', '1.0'),
    (35, 'career:teleport', 'company_career', 'http', 100, 'career:teleport.search', '1.0'),
    (36, 'career:joom', 'company_career', 'http', 100, 'career:joom.search', '1.0'),
    (37, 'career:zeptolab', 'company_career', 'http', 100, 'career:zeptolab.search', '1.0'),
    (38, 'career:abbyy', 'company_career', 'http', 100, 'career:abbyy.search', '1.0'),
    (39, 'career:ahrefs', 'company_career', 'http', 100, 'career:ahrefs.search', '1.0'),
    (40, 'career:eqvilent', 'company_career', 'http', 100, 'career:eqvilent.search', '1.0'),
    (41, 'career:humansignal', 'company_career', 'http', 100, 'career:humansignal.search', '1.0'),
    (42, 'career:altenar', 'company_career', 'http', 100, 'career:altenar.search', '1.0'),
    (43, 'career:synder', 'company_career', 'http', 100, 'career:synder.search', '1.0'),
    (44, 'career:crystal', 'company_career', 'http', 100, 'career:crystal.search', '1.0'),
    (45, 'career:synthesized', 'company_career', 'http', 100, 'career:synthesized.search', '1.0'),
    (46, 'career:tradingview', 'company_career', 'http', 100, 'career:tradingview.search', '1.0'),
    (47, 'career:osome', 'company_career', 'http', 100, 'career:osome.search', '1.0'),
    (48, 'career:sumsub', 'company_career', 'http', 100, 'career:sumsub.search', '1.0'),
    (49, 'career:unlimint', 'company_career', 'http', 100, 'career:unlimint.search', '1.0'),
    (50, 'career:mapbox', 'company_career', 'http', 100, 'career:mapbox.search', '1.0'),
    (51, 'career:homebuddy', 'company_career', 'http', 100, 'career:homebuddy.search', '1.0'),
    (52, 'career:lyka', 'company_career', 'http', 100, 'career:lyka.search', '1.0'),
    (53, 'career:lokalise', 'company_career', 'http', 100, 'career:lokalise.search', '1.0'),
    (54, 'career:adtech-holding', 'company_career', 'http', 100, 'career:adtech-holding.search', '1.0'),
    (55, 'career:onemarketdata', 'company_career', 'http', 100, 'career:onemarketdata.search', '1.0'),
    (56, 'career:flo-health', 'company_career', 'http', 100, 'career:flo-health.search', '1.0'),
    (57, 'career:pandadoc', 'company_career', 'http', 100, 'career:pandadoc.search', '1.0'),
    (58, 'career:wrike', 'company_career', 'http', 100, 'career:wrike.search', '1.0'),
    (59, 'career:thesoul-publishing', 'company_career', 'http', 100, 'career:thesoul-publishing.search', '1.0'),
    (60, 'career:semrush', 'company_career', 'http', 100, 'career:semrush.search', '1.0'),
    (61, 'career:quadcode', 'company_career', 'http', 100, 'career:quadcode.search', '1.0'),
    (62, 'career:vivid-money', 'company_career', 'http', 100, 'career:vivid-money.search', '1.0'),
    (63, 'career:sidestream', 'company_career', 'http', 100, 'career:sidestream.search', '1.0'),
    (64, 'career:sbk-parus', 'company_career', 'http', 100, 'career:sbk-parus.search', '1.0'),
    (65, 'career:softmall', 'company_career', 'http', 100, 'career:softmall.search', '1.0'),
    (66, 'career:retnnet', 'company_career', 'http', 100, 'career:retnnet.search', '1.0'),
    (67, 'career:znanie', 'company_career', 'http', 100, 'career:znanie.search', '1.0'),
    (68, 'career:nii-spetsvuzavtomatika', 'company_career', 'http', 100, 'career:nii-spetsvuzavtomatika.search', '1.0'),
    (69, 'career:social-discovery-group', 'company_career', 'http', 100, 'career:social-discovery-group.search', '1.0'),
    (70, 'career:prequel', 'company_career', 'http', 100, 'career:prequel.search', '1.0'),
    (71, 'career:veryfi', 'company_career', 'http', 100, 'career:veryfi.search', '1.0'),
    (72, 'career:switchboard', 'company_career', 'http', 50, 'career:switchboard.search', '1.0'),
    (73, 'career:apicworld', 'company_career', 'http', 50, 'career:apicworld.search', '1.0'),
    (74, 'career:smartrecruiters', 'company_career', 'http', 100, 'career:smartrecruiters.search', '1.0'),
    (75, 'career:themis-insight', 'company_career', 'http', 100, 'career:themis-insight.search', '1.0'),
    (76, 'career:bunq', 'company_career', 'http', 100, 'career:bunq.search', '1.0'),
    (77, 'career:bosch', 'company_career', 'http', 100, 'career:bosch.search', '1.0'),
    (78, 'career:visa', 'company_career', 'http', 100, 'career:visa.search', '1.0'),
    (79, 'career:tripleten', 'company_career', 'http', 300, 'career:tripleten.search', '1.0'),
    (80, 'career:comm-it', 'company_career', 'http', 200, 'career:comm-it.search', '1.0'),
    (81, 'career:progress', 'company_career', 'http', 100, 'career:progress.search', '1.0'),
    (82, 'career:visionist', 'company_career', 'http', 100, 'career:visionist.search', '1.0'),
    (83, 'career:foundation-ai', 'company_career', 'http', 100, 'career:foundation-ai.search', '1.0'),
    (84, 'career:imanage', 'company_career', 'http', 100, 'career:imanage.search', '1.0'),
    (85, 'career:pairsoft', 'company_career', 'http', 100, 'career:pairsoft.search', '1.0'),
    (86, 'career:expleo', 'company_career', 'http', 100, 'career:expleo.search', '1.0'),
    (87, 'career:epe-consulting', 'company_career', 'http', 100, 'career:epe-consulting.search', '1.0'),
    (88, 'career:western-southern', 'company_career', 'http', 200, 'career:western-southern.search', '1.0'),
    (89, 'career:keylogic', 'company_career', 'http', 100, 'career:keylogic.search', '1.0'),
    (90, 'career:navstar', 'company_career', 'http', 100, 'career:navstar.search', '1.0'),
    (91, 'career:aurora-flight-sciences', 'company_career', 'http', 100, 'career:aurora-flight-sciences.search', '1.0'),
    (92, 'career:pictet', 'company_career', 'http', 200, 'career:pictet.search', '1.0'),
    (93, 'career:brevard-county', 'company_career', 'http', 200, 'career:brevard-county.search', '1.0'),
    (94, 'career:mindray', 'company_career', 'http', 200, 'career:mindray.search', '1.0'),
    (95, 'career:integrate', 'company_career', 'http', 100, 'career:integrate.search', '1.0'),
    (96, 'career:avalanche-studios', 'company_career', 'http', 100, 'career:avalanche-studios.search', '1.0'),
    (97, 'career:teramind', 'company_career', 'http', 100, 'career:teramind.search', '1.0'),
    (98, 'career:filevine', 'company_career', 'http', 100, 'career:filevine.search', '1.0'),
    (99, 'career:skydance', 'company_career', 'http', 100, 'career:skydance.search', '1.0'),
    (100, 'career:ramp', 'company_career', 'http', 200, 'career:ramp.search', '1.0'),
    (101, 'career:street-child', 'company_career', 'http', 100, 'career:street-child.search', '1.0'),
    (102, 'career:pepperstone', 'company_career', 'http', 100, 'career:pepperstone.search', '1.0'),
    (103, 'career:obrela', 'company_career', 'http', 100, 'career:obrela.search', '1.0'),
    (104, 'career:grid', 'company_career', 'http', 100, 'career:grid.search', '1.0'),
    (105, 'career:hygraph', 'company_career', 'http', 100, 'career:hygraph.search', '1.0'),
    (106, 'career:great-minds', 'company_career', 'http', 100, 'career:great-minds.search', '1.0'),
    (107, 'career:apify', 'company_career', 'http', 100, 'career:apify.search', '1.0'),
    (108, 'career:nielseniq', 'company_career', 'http', 500, 'career:nielseniq.search', '1.0'),
    (109, 'career:software-finder', 'company_career', 'http', 100, 'career:software-finder.search', '1.0'),
    (110, 'career:the-studio', 'company_career', 'http', 100, 'career:the-studio.search', '1.0'),
    (111, 'career:realitymine', 'company_career', 'http', 100, 'career:realitymine.search', '1.0'),
    (112, 'career:tixtrack', 'company_career', 'http', 100, 'career:tixtrack.search', '1.0'),
    (113, 'career:stark', 'company_career', 'http', 100, 'career:stark.search', '1.0'),
    (114, 'career:entrix', 'company_career', 'http', 100, 'career:entrix.search', '1.0'),
    (115, 'career:360t', 'company_career', 'http', 100, 'career:360t.search', '1.0'),
    (116, 'career:agile-robots', 'company_career', 'http', 100, 'career:agile-robots.search', '1.0'),
    (117, 'career:moser-consulting', 'company_career', 'http', 100, 'career:moser-consulting.search', '1.0'),
    (118, 'career:notably', 'company_career', 'http', 100, 'career:notably.search', '1.0'),
    (119, 'career:hioperator', 'company_career', 'http', 100, 'career:hioperator.search', '1.0'),
    (120, 'career:egnyte', 'company_career', 'http', 100, 'career:egnyte.search', '1.0'),
    (121, 'career:point-of-rental', 'company_career', 'http', 100, 'career:point-of-rental.search', '1.0'),
    (122, 'career:webmd', 'company_career', 'http', 100, 'career:webmd.search', '1.0'),
    (123, 'career:reveal', 'company_career', 'http', 100, 'career:reveal.search', '1.0'),
    (124, 'career:nro', 'company_career', 'http', 100, 'career:nro.search', '1.0'),
    (125, 'career:sphere', 'company_career', 'http', 100, 'career:sphere.search', '1.0'),
    (126, 'career:public-citizen', 'company_career', 'http', 100, 'career:public-citizen.search', '1.0'),
    (127, 'career:labelmaster', 'company_career', 'http', 100, 'career:labelmaster.search', '1.0'),
    (128, 'career:sfo', 'company_career', 'http', 100, 'career:sfo.search', '1.0'),
    (129, 'career:carecentrix', 'company_career', 'http', 100, 'career:carecentrix.search', '1.0'),
    (130, 'career:rambus', 'company_career', 'http', 100, 'career:rambus.search', '1.0'),
    (131, 'career:nvidia', 'company_career', 'http', 100, 'career:nvidia.search', '1.0'),
    (132, 'career:instacart', 'company_career', 'http', 100, 'career:instacart.search', '1.0'),
    (133, 'career:vast-data', 'company_career', 'http', 200, 'career:vast-data.search', '1.0'),
    (134, 'career:outerbox', 'company_career', 'http', 100, 'career:outerbox.search', '1.0'),
    (135, 'career:surecomp', 'company_career', 'http', 100, 'career:surecomp.search', '1.0'),
    (136, 'career:routine-labs', 'company_career', 'http', 100, 'career:routine-labs.search', '1.0'),
    (137, 'career:goodweek', 'company_career', 'http', 100, 'career:goodweek.search', '1.0'),
    (138, 'career:yld', 'company_career', 'http', 100, 'career:yld.search', '1.0'),
    (139, 'career:openhc', 'company_career', 'http', 100, 'career:openhc.search', '1.0'),
    (140, 'career:plus8soft', 'company_career', 'http', 100, 'career:plus8soft.search', '1.0'),
    (141, 'career:fjx-group', 'company_career', 'http', 100, 'career:fjx-group.search', '1.0'),
    (142, 'career:overgear', 'company_career', 'http', 100, 'career:overgear.search', '1.0'),
    (143, 'career:sakura-games', 'company_career', 'http', 100, 'career:sakura-games.search', '1.0'),
    (144, 'career:mediacom', 'company_career', 'http', 200, 'career:mediacom.search', '1.0'),
    (145, 'career:internews', 'company_career', 'http', 100, 'career:internews.search', '1.0'),
    (146, 'career:great-hearts', 'company_career', 'http', 100, 'career:great-hearts.search', '1.0'),
    (147, 'career:almarai', 'company_career', 'http', 200, 'career:almarai.search', '1.0'),
    (148, 'career:esa', 'company_career', 'http', 200, 'career:esa.search', '1.0'),
    (149, 'career:alan', 'company_career', 'http', 100, 'career:alan.search', '1.0'),
    (150, 'career:algolia', 'company_career', 'http', 100, 'career:algolia.search', '1.0'),
    (151, 'career:amplitude', 'company_career', 'http', 100, 'career:amplitude.search', '1.0'),
    (152, 'career:anthropic', 'company_career', 'http', 100, 'career:anthropic.search', '1.0'),
    (153, 'career:anyscale', 'company_career', 'http', 100, 'career:anyscale.search', '1.0'),
    (154, 'career:astral', 'company_career', 'http', 100, 'career:astral.search', '1.0'),
    (155, 'career:backmarket', 'company_career', 'http', 100, 'career:backmarket.search', '1.0'),
    (156, 'career:baseten', 'company_career', 'http', 100, 'career:baseten.search', '1.0'),
    (157, 'career:bolt', 'company_career', 'http', 100, 'career:bolt.search', '1.0'),
    (158, 'career:brex', 'company_career', 'http', 100, 'career:brex.search', '1.0'),
    (159, 'career:clerk', 'company_career', 'http', 100, 'career:clerk.search', '1.0'),
    (160, 'career:coda', 'company_career', 'http', 100, 'career:coda.search', '1.0'),
    (161, 'career:cohere', 'company_career', 'http', 100, 'career:cohere.search', '1.0'),
    (162, 'career:contentful', 'company_career', 'http', 100, 'career:contentful.search', '1.0'),
    (163, 'career:convex', 'company_career', 'http', 100, 'career:convex.search', '1.0'),
    (164, 'career:cypress', 'company_career', 'http', 100, 'career:cypress.search', '1.0'),
    (165, 'career:datadog', 'company_career', 'http', 100, 'career:datadog.search', '1.0'),
    (166, 'career:deel', 'company_career', 'http', 100, 'career:deel.search', '1.0'),
    (167, 'career:deepgram', 'company_career', 'http', 100, 'career:deepgram.search', '1.0'),
    (168, 'career:doctolib', 'company_career', 'http', 100, 'career:doctolib.search', '1.0'),
    (169, 'career:elevenlabs', 'company_career', 'http', 100, 'career:elevenlabs.search', '1.0'),
    (170, 'career:figma', 'company_career', 'http', 100, 'career:figma.search', '1.0'),
    (171, 'career:flink', 'company_career', 'http', 100, 'career:flink.search', '1.0'),
    (172, 'career:framer', 'company_career', 'http', 100, 'career:framer.search', '1.0'),
    (173, 'career:fullstory', 'company_career', 'http', 100, 'career:fullstory.search', '1.0'),
    (174, 'career:getyourguide', 'company_career', 'http', 100, 'career:getyourguide.search', '1.0'),
    (175, 'career:gorgias', 'company_career', 'http', 100, 'career:gorgias.search', '1.0'),
    (176, 'career:grafana', 'company_career', 'http', 100, 'career:grafana.search', '1.0'),
    (177, 'career:gusto', 'company_career', 'http', 100, 'career:gusto.search', '1.0'),
    (178, 'career:huggingface', 'company_career', 'http', 100, 'career:huggingface.search', '1.0'),
    (179, 'career:intercom', 'company_career', 'http', 100, 'career:intercom.search', '1.0'),
    (180, 'career:kahoot', 'company_career', 'http', 100, 'career:kahoot.search', '1.0'),
    (181, 'career:klarna', 'company_career', 'http', 100, 'career:klarna.search', '1.0'),
    (182, 'career:langchain', 'company_career', 'http', 100, 'career:langchain.search', '1.0'),
    (183, 'career:lattice', 'company_career', 'http', 100, 'career:lattice.search', '1.0'),
    (184, 'career:launchdarkly', 'company_career', 'http', 100, 'career:launchdarkly.search', '1.0'),
    (185, 'career:linear', 'company_career', 'http', 100, 'career:linear.search', '1.0'),
    (186, 'career:loom', 'company_career', 'http', 100, 'career:loom.search', '1.0'),
    (187, 'career:mercury', 'company_career', 'http', 100, 'career:mercury.search', '1.0'),
    (188, 'career:miro', 'company_career', 'http', 100, 'career:miro.search', '1.0'),
    (189, 'career:mistral', 'company_career', 'http', 100, 'career:mistral.search', '1.0'),
    (190, 'career:mixpanel', 'company_career', 'http', 100, 'career:mixpanel.search', '1.0'),
    (191, 'career:monzo', 'company_career', 'http', 100, 'career:monzo.search', '1.0'),
    (192, 'career:n26', 'company_career', 'http', 100, 'career:n26.search', '1.0'),
    (193, 'career:notion', 'company_career', 'http', 100, 'career:notion.search', '1.0'),
    (194, 'career:openai', 'company_career', 'http', 100, 'career:openai.search', '1.0'),
    (195, 'career:oyster', 'company_career', 'http', 100, 'career:oyster.search', '1.0'),
    (196, 'career:personio', 'company_career', 'http', 100, 'career:personio.search', '1.0'),
    (197, 'career:pinecone', 'company_career', 'http', 100, 'career:pinecone.search', '1.0'),
    (198, 'career:pitch', 'company_career', 'http', 100, 'career:pitch.search', '1.0'),
    (199, 'career:plaid', 'company_career', 'http', 100, 'career:plaid.search', '1.0'),
    (200, 'career:pleo', 'company_career', 'http', 100, 'career:pleo.search', '1.0'),
    (201, 'career:posthog', 'company_career', 'http', 100, 'career:posthog.search', '1.0'),
    (202, 'career:postman', 'company_career', 'http', 100, 'career:postman.search', '1.0'),
    (203, 'career:qdrant', 'company_career', 'http', 100, 'career:qdrant.search', '1.0'),
    (204, 'career:qonto', 'company_career', 'http', 100, 'career:qonto.search', '1.0'),
    (205, 'career:remote', 'company_career', 'http', 100, 'career:remote.search', '1.0'),
    (206, 'career:replit', 'company_career', 'http', 100, 'career:replit.search', '1.0'),
    (207, 'career:resend', 'company_career', 'http', 100, 'career:resend.search', '1.0'),
    (208, 'career:revolut', 'company_career', 'http', 100, 'career:revolut.search', '1.0'),
    (209, 'career:rippling', 'company_career', 'http', 100, 'career:rippling.search', '1.0'),
    (210, 'career:segment', 'company_career', 'http', 100, 'career:segment.search', '1.0'),
    (211, 'career:sentry', 'company_career', 'http', 100, 'career:sentry.search', '1.0'),
    (212, 'career:smallpdf', 'company_career', 'http', 100, 'career:smallpdf.search', '1.0'),
    (213, 'career:snyk', 'company_career', 'http', 100, 'career:snyk.search', '1.0'),
    (214, 'career:spendesk', 'company_career', 'http', 100, 'career:spendesk.search', '1.0'),
    (215, 'career:stripe', 'company_career', 'http', 100, 'career:stripe.search', '1.0'),
    (216, 'career:sumup', 'company_career', 'http', 100, 'career:sumup.search', '1.0'),
    (217, 'career:supabase', 'company_career', 'http', 100, 'career:supabase.search', '1.0'),
    (218, 'career:swile', 'company_career', 'http', 100, 'career:swile.search', '1.0'),
    (219, 'career:temporal', 'company_career', 'http', 100, 'career:temporal.search', '1.0'),
    (220, 'career:typeform', 'company_career', 'http', 100, 'career:typeform.search', '1.0'),
    (221, 'career:vanta', 'company_career', 'http', 100, 'career:vanta.search', '1.0'),
    (222, 'career:vercel', 'company_career', 'http', 100, 'career:vercel.search', '1.0'),
    (223, 'career:vinted', 'company_career', 'http', 100, 'career:vinted.search', '1.0'),
    (224, 'career:weaviate', 'company_career', 'http', 100, 'career:weaviate.search', '1.0'),
    (225, 'career:webflow', 'company_career', 'http', 100, 'career:webflow.search', '1.0'),
    (226, 'career:wise', 'company_career', 'http', 100, 'career:wise.search', '1.0'),
    (227, 'career:wolt', 'company_career', 'http', 100, 'career:wolt.search', '1.0');

INSERT INTO countries (country_code, display_name, search_enabled)
VALUES
    ('RU', 'Russia', 1),
    ('AM', 'Armenia', 1);

INSERT INTO source_countries (source_id, country_order, country)
VALUES
    ('habr_career', 0, 'RU'),
    ('hh_ru', 0, 'RU'),
    ('career:vk', 0, 'RU'),
    ('career:ibs', 0, 'RU'),
    ('career:amocrm', 0, 'RU'),
    ('hirehi', 0, 'RU'),
    ('staff_am', 0, 'AM'),
    ('career:sbk-parus', 0, 'RU'),
    ('career:softmall', 0, 'RU'),
    ('career:retnnet', 0, 'RU'),
    ('career:znanie', 0, 'RU'),
    ('career:nii-spetsvuzavtomatika', 0, 'RU');

INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
VALUES
    ('habr_career', 0, 'query', 'native_request'),
    ('habr_career', 1, 'grades', 'native_request'),
    ('habr_career', 2, 'compensation', 'structured_output'),
    ('habr_career', 3, 'published_since', 'structured_output'),
    ('habr_career', 4, 'relocation', 'unsupported'),
    ('habr_career', 5, 'work_formats', 'structured_output'),
    ('habr_career', 6, 'remote_scopes', 'structured_output'),
    ('habr_career', 7, 'vacancy_geographies', 'structured_output'),
    ('hh_ru', 0, 'query', 'native_request'),
    ('hh_ru', 1, 'grades', 'structured_output'),
    ('hh_ru', 2, 'compensation', 'structured_output'),
    ('hh_ru', 3, 'published_since', 'structured_output'),
    ('hh_ru', 4, 'relocation', 'unsupported'),
    ('hh_ru', 5, 'work_formats', 'structured_output'),
    ('hh_ru', 6, 'remote_scopes', 'structured_output'),
    ('hh_ru', 7, 'vacancy_geographies', 'structured_output'),
    ('talanto', 0, 'query', 'native_request'),
    ('talanto', 1, 'grades', 'structured_output'),
    ('talanto', 2, 'compensation', 'structured_output'),
    ('talanto', 3, 'published_since', 'structured_output'),
    ('talanto', 4, 'relocation', 'unsupported'),
    ('talanto', 5, 'work_formats', 'structured_output'),
    ('talanto', 6, 'remote_scopes', 'structured_output'),
    ('talanto', 7, 'vacancy_geographies', 'structured_output'),
    ('career:vk', 0, 'query', 'structured_output'),
    ('career:vk', 1, 'grades', 'unsupported'),
    ('career:vk', 2, 'compensation', 'unsupported'),
    ('career:vk', 3, 'published_since', 'unsupported'),
    ('career:vk', 4, 'relocation', 'unsupported'),
    ('career:vk', 5, 'work_formats', 'structured_output'),
    ('career:vk', 6, 'remote_scopes', 'structured_output'),
    ('career:vk', 7, 'vacancy_geographies', 'structured_output'),
    ('career:jetbrains', 0, 'query', 'structured_output'),
    ('career:jetbrains', 1, 'grades', 'unsupported'),
    ('career:jetbrains', 2, 'compensation', 'unsupported'),
    ('career:jetbrains', 3, 'published_since', 'structured_output'),
    ('career:jetbrains', 4, 'relocation', 'unsupported'),
    ('career:jetbrains', 5, 'work_formats', 'structured_output'),
    ('career:jetbrains', 6, 'remote_scopes', 'structured_output'),
    ('career:jetbrains', 7, 'vacancy_geographies', 'structured_output'),
    ('career:ibs', 0, 'query', 'structured_output'),
    ('career:ibs', 1, 'grades', 'unsupported'),
    ('career:ibs', 2, 'compensation', 'unsupported'),
    ('career:ibs', 3, 'published_since', 'unsupported'),
    ('career:ibs', 4, 'relocation', 'unsupported'),
    ('career:ibs', 5, 'work_formats', 'structured_output'),
    ('career:ibs', 6, 'remote_scopes', 'structured_output'),
    ('career:ibs', 7, 'vacancy_geographies', 'structured_output'),
    ('geekjob', 0, 'query', 'structured_output'),
    ('geekjob', 1, 'grades', 'unsupported'),
    ('geekjob', 2, 'compensation', 'structured_output'),
    ('geekjob', 3, 'published_since', 'structured_output'),
    ('geekjob', 4, 'relocation', 'unsupported'),
    ('geekjob', 5, 'work_formats', 'structured_output'),
    ('geekjob', 6, 'remote_scopes', 'structured_output'),
    ('geekjob', 7, 'vacancy_geographies', 'structured_output'),
    ('talento', 0, 'query', 'native_request'),
    ('talento', 1, 'grades', 'unsupported'),
    ('talento', 2, 'compensation', 'unsupported'),
    ('talento', 3, 'published_since', 'unsupported'),
    ('talento', 4, 'relocation', 'unsupported'),
    ('talento', 5, 'work_formats', 'unsupported'),
    ('talento', 6, 'remote_scopes', 'unsupported'),
    ('talento', 7, 'vacancy_geographies', 'unsupported'),
    ('finder_work', 0, 'query', 'native_request'),
    ('finder_work', 1, 'grades', 'structured_output'),
    ('finder_work', 2, 'compensation', 'structured_output'),
    ('finder_work', 3, 'published_since', 'structured_output'),
    ('finder_work', 4, 'relocation', 'unsupported'),
    ('finder_work', 5, 'work_formats', 'structured_output'),
    ('finder_work', 6, 'remote_scopes', 'structured_output'),
    ('finder_work', 7, 'vacancy_geographies', 'structured_output'),
    ('getmatch', 0, 'query', 'native_request'),
    ('getmatch', 1, 'grades', 'unsupported'),
    ('getmatch', 2, 'compensation', 'unsupported'),
    ('getmatch', 3, 'published_since', 'structured_output'),
    ('getmatch', 4, 'relocation', 'unsupported'),
    ('getmatch', 5, 'work_formats', 'structured_output'),
    ('getmatch', 6, 'remote_scopes', 'structured_output'),
    ('getmatch', 7, 'vacancy_geographies', 'structured_output'),
    ('it_jobs_uz', 0, 'query', 'native_request'),
    ('it_jobs_uz', 1, 'grades', 'structured_output'),
    ('it_jobs_uz', 2, 'compensation', 'structured_output'),
    ('it_jobs_uz', 3, 'published_since', 'structured_output'),
    ('it_jobs_uz', 4, 'relocation', 'unsupported'),
    ('it_jobs_uz', 5, 'work_formats', 'structured_output'),
    ('it_jobs_uz', 6, 'remote_scopes', 'structured_output'),
    ('it_jobs_uz', 7, 'vacancy_geographies', 'structured_output'),
    ('hirify', 0, 'query', 'native_request'),
    ('hirify', 1, 'grades', 'structured_output'),
    ('hirify', 2, 'compensation', 'structured_output'),
    ('hirify', 3, 'published_since', 'structured_output'),
    ('hirify', 4, 'relocation', 'structured_output'),
    ('hirify', 5, 'work_formats', 'structured_output'),
    ('hirify', 6, 'remote_scopes', 'structured_output'),
    ('hirify', 7, 'vacancy_geographies', 'structured_output'),
    ('jobturbo', 0, 'query', 'structured_output'),
    ('jobturbo', 1, 'grades', 'structured_output'),
    ('jobturbo', 2, 'compensation', 'structured_output'),
    ('jobturbo', 3, 'published_since', 'unsupported'),
    ('jobturbo', 4, 'relocation', 'unsupported'),
    ('jobturbo', 5, 'work_formats', 'structured_output'),
    ('jobturbo', 6, 'remote_scopes', 'structured_output'),
    ('jobturbo', 7, 'vacancy_geographies', 'unsupported'),
    ('hirehi', 0, 'query', 'native_request'),
    ('hirehi', 1, 'grades', 'structured_output'),
    ('hirehi', 2, 'compensation', 'structured_output'),
    ('hirehi', 3, 'published_since', 'unsupported'),
    ('hirehi', 4, 'relocation', 'unsupported'),
    ('hirehi', 5, 'work_formats', 'structured_output'),
    ('hirehi', 6, 'remote_scopes', 'structured_output'),
    ('hirehi', 7, 'vacancy_geographies', 'structured_output'),
    ('staff_am', 0, 'query', 'native_request'),
    ('staff_am', 1, 'grades', 'structured_output'),
    ('staff_am', 2, 'compensation', 'unsupported'),
    ('staff_am', 3, 'published_since', 'structured_output'),
    ('staff_am', 4, 'relocation', 'structured_output'),
    ('staff_am', 5, 'work_formats', 'structured_output'),
    ('staff_am', 6, 'remote_scopes', 'structured_output'),
    ('staff_am', 7, 'vacancy_geographies', 'structured_output'),
    ('career:amocrm', 0, 'query', 'structured_output'),
    ('career:amocrm', 1, 'grades', 'unsupported'),
    ('career:amocrm', 2, 'compensation', 'unsupported'),
    ('career:amocrm', 3, 'published_since', 'unsupported'),
    ('career:amocrm', 4, 'relocation', 'unsupported'),
    ('career:amocrm', 5, 'work_formats', 'unsupported'),
    ('career:amocrm', 6, 'remote_scopes', 'unsupported'),
    ('career:amocrm', 7, 'vacancy_geographies', 'structured_output'),
    ('career:coinspaid', 0, 'query', 'structured_output'),
    ('career:coinspaid', 1, 'grades', 'unsupported'),
    ('career:coinspaid', 2, 'compensation', 'unsupported'),
    ('career:coinspaid', 3, 'published_since', 'structured_output'),
    ('career:coinspaid', 4, 'relocation', 'unsupported'),
    ('career:coinspaid', 5, 'work_formats', 'structured_output'),
    ('career:coinspaid', 6, 'remote_scopes', 'structured_output'),
    ('career:coinspaid', 7, 'vacancy_geographies', 'structured_output'),
    ('career:appfollow', 0, 'query', 'structured_output'),
    ('career:appfollow', 1, 'grades', 'unsupported'),
    ('career:appfollow', 2, 'compensation', 'unsupported'),
    ('career:appfollow', 3, 'published_since', 'structured_output'),
    ('career:appfollow', 4, 'relocation', 'unsupported'),
    ('career:appfollow', 5, 'work_formats', 'structured_output'),
    ('career:appfollow', 6, 'remote_scopes', 'structured_output'),
    ('career:appfollow', 7, 'vacancy_geographies', 'structured_output'),
    ('career:airslate', 0, 'query', 'structured_output'),
    ('career:airslate', 1, 'grades', 'unsupported'),
    ('career:airslate', 2, 'compensation', 'unsupported'),
    ('career:airslate', 3, 'published_since', 'structured_output'),
    ('career:airslate', 4, 'relocation', 'unsupported'),
    ('career:airslate', 5, 'work_formats', 'structured_output'),
    ('career:airslate', 6, 'remote_scopes', 'structured_output'),
    ('career:airslate', 7, 'vacancy_geographies', 'structured_output'),
    ('career:wintermute', 0, 'query', 'structured_output'),
    ('career:wintermute', 1, 'grades', 'unsupported'),
    ('career:wintermute', 2, 'compensation', 'unsupported'),
    ('career:wintermute', 3, 'published_since', 'structured_output'),
    ('career:wintermute', 4, 'relocation', 'unsupported'),
    ('career:wintermute', 5, 'work_formats', 'structured_output'),
    ('career:wintermute', 6, 'remote_scopes', 'structured_output'),
    ('career:wintermute', 7, 'vacancy_geographies', 'structured_output'),
    ('career:truv', 0, 'query', 'structured_output'),
    ('career:truv', 1, 'grades', 'unsupported'),
    ('career:truv', 2, 'compensation', 'unsupported'),
    ('career:truv', 3, 'published_since', 'structured_output'),
    ('career:truv', 4, 'relocation', 'unsupported'),
    ('career:truv', 5, 'work_formats', 'structured_output'),
    ('career:truv', 6, 'remote_scopes', 'structured_output'),
    ('career:truv', 7, 'vacancy_geographies', 'structured_output'),
    ('career:termius', 0, 'query', 'structured_output'),
    ('career:termius', 1, 'grades', 'unsupported'),
    ('career:termius', 2, 'compensation', 'unsupported'),
    ('career:termius', 3, 'published_since', 'structured_output'),
    ('career:termius', 4, 'relocation', 'unsupported'),
    ('career:termius', 5, 'work_formats', 'structured_output'),
    ('career:termius', 6, 'remote_scopes', 'structured_output'),
    ('career:termius', 7, 'vacancy_geographies', 'structured_output'),
    ('career:outschool', 0, 'query', 'structured_output'),
    ('career:outschool', 1, 'grades', 'unsupported'),
    ('career:outschool', 2, 'compensation', 'unsupported'),
    ('career:outschool', 3, 'published_since', 'structured_output'),
    ('career:outschool', 4, 'relocation', 'unsupported'),
    ('career:outschool', 5, 'work_formats', 'structured_output'),
    ('career:outschool', 6, 'remote_scopes', 'structured_output'),
    ('career:outschool', 7, 'vacancy_geographies', 'structured_output'),
    ('career:zeroavia', 0, 'query', 'structured_output'),
    ('career:zeroavia', 1, 'grades', 'unsupported'),
    ('career:zeroavia', 2, 'compensation', 'unsupported'),
    ('career:zeroavia', 3, 'published_since', 'structured_output'),
    ('career:zeroavia', 4, 'relocation', 'unsupported'),
    ('career:zeroavia', 5, 'work_formats', 'structured_output'),
    ('career:zeroavia', 6, 'remote_scopes', 'unsupported'),
    ('career:zeroavia', 7, 'vacancy_geographies', 'structured_output'),
    ('career:wallarm', 0, 'query', 'structured_output'),
    ('career:wallarm', 1, 'grades', 'unsupported'),
    ('career:wallarm', 2, 'compensation', 'structured_output'),
    ('career:wallarm', 3, 'published_since', 'structured_output'),
    ('career:wallarm', 4, 'relocation', 'unsupported'),
    ('career:wallarm', 5, 'work_formats', 'structured_output'),
    ('career:wallarm', 6, 'remote_scopes', 'structured_output'),
    ('career:wallarm', 7, 'vacancy_geographies', 'structured_output'),
    ('career:chainstack', 0, 'query', 'structured_output'),
    ('career:chainstack', 1, 'grades', 'unsupported'),
    ('career:chainstack', 2, 'compensation', 'unsupported'),
    ('career:chainstack', 3, 'published_since', 'unsupported'),
    ('career:chainstack', 4, 'relocation', 'unsupported'),
    ('career:chainstack', 5, 'work_formats', 'structured_output'),
    ('career:chainstack', 6, 'remote_scopes', 'unsupported'),
    ('career:chainstack', 7, 'vacancy_geographies', 'unsupported'),
    ('career:3commas', 0, 'query', 'structured_output'),
    ('career:3commas', 1, 'grades', 'unsupported'),
    ('career:3commas', 2, 'compensation', 'unsupported'),
    ('career:3commas', 3, 'published_since', 'structured_output'),
    ('career:3commas', 4, 'relocation', 'unsupported'),
    ('career:3commas', 5, 'work_formats', 'structured_output'),
    ('career:3commas', 6, 'remote_scopes', 'structured_output'),
    ('career:3commas', 7, 'vacancy_geographies', 'structured_output');

WITH lever_sources(source_id) AS (
    VALUES
        ('career:collectly'),
        ('career:planner5d'),
        ('career:superannotate'),
        ('career:xsolla'),
        ('career:unlimint'),
        ('career:quadcode'),
        ('career:integrate'),
        ('career:avalanche-studios'),
        ('career:teramind'),
        ('career:filevine'),
        ('career:skydance'),
        ('career:mistral'),
        ('career:swile')
),
lever_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'structured_output'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT lever_sources.source_id, lever_criteria.criterion_order, lever_criteria.criterion, lever_criteria.capability
FROM lever_sources
CROSS JOIN lever_criteria;

WITH ashby_sources(source_id) AS (
    VALUES
        ('career:clickhouse'),
        ('career:datafold'),
        ('career:inworld'),
        ('career:luminai'),
        ('career:teleport'),
        ('career:mapbox'),
        ('career:ramp'),
        ('career:alan'),
        ('career:amplitude'),
        ('career:anyscale'),
        ('career:astral'),
        ('career:backmarket'),
        ('career:baseten'),
        ('career:bolt'),
        ('career:clerk'),
        ('career:cohere'),
        ('career:deel'),
        ('career:deepgram'),
        ('career:doctolib'),
        ('career:elevenlabs'),
        ('career:flink'),
        ('career:fullstory'),
        ('career:gorgias'),
        ('career:langchain'),
        ('career:launchdarkly'),
        ('career:linear'),
        ('career:loom'),
        ('career:mercury'),
        ('career:miro'),
        ('career:notion'),
        ('career:openai'),
        ('career:oyster'),
        ('career:pinecone'),
        ('career:plaid'),
        ('career:pleo'),
        ('career:posthog'),
        ('career:qonto'),
        ('career:replit'),
        ('career:resend'),
        ('career:sentry'),
        ('career:smallpdf'),
        ('career:snyk'),
        ('career:spendesk'),
        ('career:supabase'),
        ('career:temporal'),
        ('career:vanta'),
        ('career:vercel'),
        ('career:weaviate'),
        ('career:webflow')
),
ashby_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'structured_output'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT ashby_sources.source_id, ashby_criteria.criterion_order, ashby_criteria.criterion, ashby_criteria.capability
FROM ashby_sources
CROSS JOIN ashby_criteria;

WITH workable_sources(source_id) AS (
    VALUES
        ('career:joom'),
        ('career:zeptolab'),
        ('career:homebuddy'),
        ('career:lyka'),
        ('career:thesoul-publishing'),
        ('career:street-child'),
        ('career:pepperstone'),
        ('career:obrela'),
        ('career:coda'),
        ('career:convex'),
        ('career:cypress'),
        ('career:grafana'),
        ('career:huggingface'),
        ('career:kahoot'),
        ('career:klarna'),
        ('career:personio'),
        ('career:pitch'),
        ('career:qdrant'),
        ('career:revolut'),
        ('career:rippling'),
        ('career:segment'),
        ('career:vinted'),
        ('career:wise')
),
workable_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'structured_output'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'unsupported'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT workable_sources.source_id, workable_criteria.criterion_order, workable_criteria.criterion, workable_criteria.capability
FROM workable_sources
CROSS JOIN workable_criteria;

WITH greenhouse_sources(source_id) AS (
    VALUES
        ('career:abbyy'),
        ('career:ahrefs'),
        ('career:eqvilent'),
        ('career:humansignal'),
        ('career:lokalise'),
        ('career:flo-health'),
        ('career:pandadoc'),
        ('career:wrike'),
        ('career:algolia'),
        ('career:anthropic'),
        ('career:brex'),
        ('career:contentful'),
        ('career:datadog'),
        ('career:figma'),
        ('career:getyourguide'),
        ('career:gusto'),
        ('career:intercom'),
        ('career:lattice'),
        ('career:mixpanel'),
        ('career:monzo'),
        ('career:n26'),
        ('career:postman'),
        ('career:remote'),
        ('career:stripe'),
        ('career:sumup'),
        ('career:typeform'),
        ('career:wolt')
),
greenhouse_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'structured_output'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT greenhouse_sources.source_id, greenhouse_criteria.criterion_order, greenhouse_criteria.criterion, greenhouse_criteria.capability
FROM greenhouse_sources
CROSS JOIN greenhouse_criteria;

WITH bamboohr_sources(source_id) AS (
    VALUES
        ('career:adtech-holding'),
        ('career:altenar'),
        ('career:synder'),
        ('career:onemarketdata'),
        ('career:apify')
),
bamboohr_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'unsupported'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'unsupported'),
        (7, 'vacancy_geographies', 'unsupported')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT bamboohr_sources.source_id, bamboohr_criteria.criterion_order, bamboohr_criteria.criterion, bamboohr_criteria.capability
FROM bamboohr_sources
CROSS JOIN bamboohr_criteria;

WITH teamtailor_sources(source_id) AS (
    VALUES
        ('career:crystal'),
        ('career:synthesized'),
        ('career:tradingview'),
        ('career:osome'),
        ('career:sumsub'),
        ('career:software-finder'),
        ('career:the-studio'),
        ('career:realitymine'),
        ('career:tixtrack')
),
teamtailor_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'unsupported'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT teamtailor_sources.source_id, teamtailor_criteria.criterion_order, teamtailor_criteria.criterion, teamtailor_criteria.capability
FROM teamtailor_sources
CROSS JOIN teamtailor_criteria;

WITH workday_sources(source_id) AS (
    VALUES
        ('career:semrush'),
        ('career:nvidia')
),
workday_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'native_request'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'structured_output'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT workday_sources.source_id, workday_criteria.criterion_order, workday_criteria.criterion, workday_criteria.capability
FROM workday_sources
CROSS JOIN workday_criteria;

WITH personio_sources(source_id) AS (
    VALUES
        ('career:vivid-money'),
        ('career:stark'),
        ('career:entrix'),
        ('career:360t'),
        ('career:agile-robots'),
        ('career:framer')
),
personio_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'structured_output'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT personio_sources.source_id, personio_criteria.criterion_order, personio_criteria.criterion, personio_criteria.capability
FROM personio_sources
CROSS JOIN personio_criteria;

WITH join_sources(source_id) AS (
    VALUES
        ('career:sidestream'),
        ('career:routine-labs'),
        ('career:goodweek'),
        ('career:yld')
),
join_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'structured_output'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT join_sources.source_id, join_criteria.criterion_order, join_criteria.criterion, join_criteria.capability
FROM join_sources
CROSS JOIN join_criteria;

WITH dreamjob_sources(source_id) AS (
    VALUES
        ('career:sbk-parus'),
        ('career:softmall'),
        ('career:retnnet'),
        ('career:znanie'),
        ('career:nii-spetsvuzavtomatika'),
        ('career:openhc')
),
dreamjob_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'structured_output'),
        (3, 'published_since', 'structured_output'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT dreamjob_sources.source_id, dreamjob_criteria.criterion_order, dreamjob_criteria.criterion, dreamjob_criteria.capability
FROM dreamjob_sources
CROSS JOIN dreamjob_criteria;

WITH jsonld_jobposting_sources(source_id) AS (
    VALUES
        ('career:social-discovery-group')
),
jsonld_jobposting_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'structured_output'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT
    jsonld_jobposting_sources.source_id,
    jsonld_jobposting_criteria.criterion_order,
    jsonld_jobposting_criteria.criterion,
    jsonld_jobposting_criteria.capability
FROM jsonld_jobposting_sources
CROSS JOIN jsonld_jobposting_criteria;

WITH ycombinator_sources(source_id) AS (
    VALUES
        ('career:prequel'),
        ('career:veryfi'),
        ('career:instacart')
),
ycombinator_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'structured_output'),
        (3, 'published_since', 'unsupported'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT
    ycombinator_sources.source_id,
    ycombinator_criteria.criterion_order,
    ycombinator_criteria.criterion,
    ycombinator_criteria.capability
FROM ycombinator_sources
CROSS JOIN ycombinator_criteria;

WITH breezy_sources(source_id) AS (
    VALUES
        ('career:switchboard'),
        ('career:themis-insight'),
        ('career:moser-consulting'),
        ('career:notably'),
        ('career:hioperator')
),
breezy_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'structured_output'),
        (3, 'published_since', 'unsupported'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'unsupported'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT
    breezy_sources.source_id,
    breezy_criteria.criterion_order,
    breezy_criteria.criterion,
    breezy_criteria.capability
FROM breezy_sources
CROSS JOIN breezy_criteria;

WITH huntflow_sources(source_id) AS (
    VALUES
        ('career:apicworld'),
        ('career:plus8soft'),
        ('career:fjx-group'),
        ('career:overgear'),
        ('career:sakura-games')
),
huntflow_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'unsupported'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT
    huntflow_sources.source_id,
    huntflow_criteria.criterion_order,
    huntflow_criteria.criterion,
    huntflow_criteria.capability
FROM huntflow_sources
CROSS JOIN huntflow_criteria;

WITH smartrecruiters_sources(source_id) AS (
    VALUES
        ('career:smartrecruiters'),
        ('career:bosch'),
        ('career:visa'),
        ('career:nielseniq')
),
smartrecruiters_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'structured_output'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT
    smartrecruiters_sources.source_id,
    smartrecruiters_criteria.criterion_order,
    smartrecruiters_criteria.criterion,
    smartrecruiters_criteria.capability
FROM smartrecruiters_sources
CROSS JOIN smartrecruiters_criteria;

WITH recruitee_sources(source_id) AS (
    VALUES
        ('career:bunq'),
        ('career:grid'),
        ('career:hygraph'),
        ('career:great-minds')
),
recruitee_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'structured_output'),
        (3, 'published_since', 'structured_output'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT
    recruitee_sources.source_id,
    recruitee_criteria.criterion_order,
    recruitee_criteria.criterion,
    recruitee_criteria.capability
FROM recruitee_sources
CROSS JOIN recruitee_criteria;

WITH comeet_sources(source_id) AS (
    VALUES
        ('career:tripleten'),
        ('career:comm-it'),
        ('career:vast-data'),
        ('career:outerbox'),
        ('career:surecomp')
),
comeet_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'structured_output'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT
    comeet_sources.source_id,
    comeet_criteria.criterion_order,
    comeet_criteria.criterion,
    comeet_criteria.capability
FROM comeet_sources
CROSS JOIN comeet_criteria;

WITH jobvite_sources(source_id) AS (
    VALUES
        ('career:progress'),
        ('career:visionist'),
        ('career:egnyte'),
        ('career:point-of-rental'),
        ('career:webmd'),
        ('career:reveal')
),
jobvite_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'unsupported'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT
    jobvite_sources.source_id,
    jobvite_criteria.criterion_order,
    jobvite_criteria.criterion,
    jobvite_criteria.capability
FROM jobvite_sources
CROSS JOIN jobvite_criteria;

WITH jazzhr_sources(source_id) AS (
    VALUES
        ('career:foundation-ai'),
        ('career:imanage'),
        ('career:pairsoft'),
        ('career:nro'),
        ('career:sphere'),
        ('career:public-citizen'),
        ('career:labelmaster')
),
jazzhr_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'unsupported'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT
    jazzhr_sources.source_id,
    jazzhr_criteria.criterion_order,
    jazzhr_criteria.criterion,
    jazzhr_criteria.capability
FROM jazzhr_sources
CROSS JOIN jazzhr_criteria;

WITH icims_sources(source_id) AS (
    VALUES
        ('career:expleo'),
        ('career:epe-consulting'),
        ('career:western-southern'),
        ('career:sfo'),
        ('career:carecentrix'),
        ('career:rambus')
),
icims_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'unsupported'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT
    icims_sources.source_id,
    icims_criteria.criterion_order,
    icims_criteria.criterion,
    icims_criteria.capability
FROM icims_sources
CROSS JOIN icims_criteria;

WITH taleo_sources(source_id) AS (
    VALUES
        ('career:keylogic'),
        ('career:navstar'),
        ('career:aurora-flight-sciences'),
        ('career:mediacom'),
        ('career:internews'),
        ('career:great-hearts')
),
taleo_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'unsupported'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT
    taleo_sources.source_id,
    taleo_criteria.criterion_order,
    taleo_criteria.criterion,
    taleo_criteria.capability
FROM taleo_sources
CROSS JOIN taleo_criteria;

WITH successfactors_sources(source_id) AS (
    VALUES
        ('career:pictet'),
        ('career:brevard-county'),
        ('career:mindray'),
        ('career:almarai'),
        ('career:esa')
),
successfactors_criteria(criterion_order, criterion, capability) AS (
    VALUES
        (0, 'query', 'structured_output'),
        (1, 'grades', 'unsupported'),
        (2, 'compensation', 'unsupported'),
        (3, 'published_since', 'structured_output'),
        (4, 'relocation', 'unsupported'),
        (5, 'work_formats', 'structured_output'),
        (6, 'remote_scopes', 'structured_output'),
        (7, 'vacancy_geographies', 'structured_output')
)
INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT
    successfactors_sources.source_id,
    successfactors_criteria.criterion_order,
    successfactors_criteria.criterion,
    successfactors_criteria.capability
FROM successfactors_sources
CROSS JOIN successfactors_criteria;

INSERT INTO source_required_fixture_kinds (source_id, kind)
VALUES
    ('habr_career', 'no_results'),
    ('habr_career', 'pagination'),
    ('habr_career', 'detail'),
    ('habr_career', 'optional_fields'),
    ('hh_ru', 'no_results'),
    ('hh_ru', 'pagination'),
    ('hh_ru', 'detail'),
    ('hh_ru', 'optional_fields'),
    ('hh_ru', 'blocked'),
    ('talanto', 'no_results'),
    ('talanto', 'detail'),
    ('career:vk', 'pagination'),
    ('career:vk', 'detail'),
    ('geekjob', 'no_results'),
    ('geekjob', 'detail'),
    ('talento', 'no_results'),
    ('talento', 'detail'),
    ('finder_work', 'no_results'),
    ('finder_work', 'detail'),
    ('getmatch', 'no_results'),
    ('it_jobs_uz', 'no_results'),
    ('hirify', 'no_results'),
    ('hirify', 'detail'),
    ('jobturbo', 'no_results'),
    ('hirehi', 'no_results'),
    ('hirehi', 'detail'),
    ('staff_am', 'no_results'),
    ('staff_am', 'detail'),
    ('career:ibs', 'pagination'),
    ('career:ibs', 'detail'),
    ('career:amocrm', 'detail'),
    ('career:appfollow', 'detail'),
    ('career:tradingview', 'pagination'),
    ('career:osome', 'pagination'),
    ('career:sumsub', 'pagination'),
    ('career:semrush', 'pagination'),
    ('career:semrush', 'detail'),
    ('career:vivid-money', 'detail'),
    ('career:sidestream', 'detail'),
    ('career:sbk-parus', 'detail'),
    ('career:softmall', 'detail'),
    ('career:retnnet', 'detail'),
    ('career:znanie', 'detail'),
    ('career:nii-spetsvuzavtomatika', 'pagination'),
    ('career:nii-spetsvuzavtomatika', 'detail'),
    ('career:visionist', 'pagination'),
    ('career:epe-consulting', 'pagination'),
    ('career:western-southern', 'pagination'),
    ('career:keylogic', 'pagination'),
    ('career:navstar', 'pagination'),
    ('career:aurora-flight-sciences', 'pagination');

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
        'hh_ru-detail',
        'detail',
        'tests/v2/fixtures/scrapers/hh_ru/detail/response.html',
        'tests/v2/fixtures/scrapers/hh_ru/detail/meta.json',
        'tests/v2/fixtures/scrapers/hh_ru/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'hh_ru',
        4,
        'hh_ru-blocked',
        'blocked',
        'tests/v2/fixtures/scrapers/hh_ru/blocked/response.html',
        'tests/v2/fixtures/scrapers/hh_ru/blocked/meta.json',
        'tests/v2/fixtures/scrapers/hh_ru/blocked/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'hh_ru',
        5,
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
        'tests/v2/fixtures/scrapers/career_vk/success/response.json',
        'tests/v2/fixtures/scrapers/career_vk/success/meta.json',
        'tests/v2/fixtures/scrapers/career_vk/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:vk',
        1,
        'career:vk-pagination',
        'pagination',
        'tests/v2/fixtures/scrapers/career_vk/pagination/response.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination/meta.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:vk',
        2,
        'career:vk-pagination-offset-50',
        'pagination',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_50/response.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_50/meta.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_50/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:vk',
        3,
        'career:vk-pagination-offset-75',
        'pagination',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_75/response.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_75/meta.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_75/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:vk',
        4,
        'career:vk-pagination-offset-100',
        'pagination',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_100/response.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_100/meta.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_100/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:vk',
        5,
        'career:vk-pagination-offset-125',
        'pagination',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_125/response.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_125/meta.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_125/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:vk',
        6,
        'career:vk-pagination-offset-150',
        'pagination',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_150/response.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_150/meta.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_150/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:vk',
        7,
        'career:vk-pagination-offset-175',
        'pagination',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_175/response.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_175/meta.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_175/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:vk',
        8,
        'career:vk-pagination-offset-200',
        'pagination',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_200/response.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_200/meta.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_200/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:vk',
        9,
        'career:vk-pagination-offset-225',
        'pagination',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_225/response.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_225/meta.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_225/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:vk',
        10,
        'career:vk-pagination-offset-250',
        'pagination',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_250/response.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_250/meta.json',
        'tests/v2/fixtures/scrapers/career_vk/pagination_offset_250/expected.raw.json',
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
        'talanto',
        2,
        'talanto-detail',
        'detail',
        'tests/v2/fixtures/scrapers/talanto/detail/response.html',
        'tests/v2/fixtures/scrapers/talanto/detail/meta.json',
        'tests/v2/fixtures/scrapers/talanto/detail/expected.raw.json',
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
    ),
    (
        'geekjob',
        2,
        'geekjob-detail',
        'detail',
        'tests/v2/fixtures/scrapers/geekjob/detail/response.html',
        'tests/v2/fixtures/scrapers/geekjob/detail/meta.json',
        'tests/v2/fixtures/scrapers/geekjob/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'talento',
        0,
        'talento-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/talento/success/response.html',
        'tests/v2/fixtures/scrapers/talento/success/meta.json',
        'tests/v2/fixtures/scrapers/talento/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'talento',
        1,
        'talento-no_results',
        'no_results',
        'tests/v2/fixtures/scrapers/talento/no_results/response.html',
        'tests/v2/fixtures/scrapers/talento/no_results/meta.json',
        'tests/v2/fixtures/scrapers/talento/no_results/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'finder_work',
        0,
        'finder_work-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/finder_work/success/response.json',
        'tests/v2/fixtures/scrapers/finder_work/success/meta.json',
        'tests/v2/fixtures/scrapers/finder_work/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'finder_work',
        1,
        'finder_work-no_results',
        'no_results',
        'tests/v2/fixtures/scrapers/finder_work/no_results/response.json',
        'tests/v2/fixtures/scrapers/finder_work/no_results/meta.json',
        'tests/v2/fixtures/scrapers/finder_work/no_results/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'getmatch',
        0,
        'getmatch-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/getmatch/success/response.json',
        'tests/v2/fixtures/scrapers/getmatch/success/meta.json',
        'tests/v2/fixtures/scrapers/getmatch/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'getmatch',
        1,
        'getmatch-no_results',
        'no_results',
        'tests/v2/fixtures/scrapers/getmatch/no_results/response.json',
        'tests/v2/fixtures/scrapers/getmatch/no_results/meta.json',
        'tests/v2/fixtures/scrapers/getmatch/no_results/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'it_jobs_uz',
        0,
        'it_jobs_uz-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/it_jobs_uz/success/response.json',
        'tests/v2/fixtures/scrapers/it_jobs_uz/success/meta.json',
        'tests/v2/fixtures/scrapers/it_jobs_uz/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'it_jobs_uz',
        1,
        'it_jobs_uz-no_results',
        'no_results',
        'tests/v2/fixtures/scrapers/it_jobs_uz/no_results/response.json',
        'tests/v2/fixtures/scrapers/it_jobs_uz/no_results/meta.json',
        'tests/v2/fixtures/scrapers/it_jobs_uz/no_results/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'hirify',
        0,
        'hirify-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/hirify/success/response.json',
        'tests/v2/fixtures/scrapers/hirify/success/meta.json',
        'tests/v2/fixtures/scrapers/hirify/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'hirify',
        1,
        'hirify-no_results',
        'no_results',
        'tests/v2/fixtures/scrapers/hirify/no_results/response.json',
        'tests/v2/fixtures/scrapers/hirify/no_results/meta.json',
        'tests/v2/fixtures/scrapers/hirify/no_results/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'hirify',
        2,
        'hirify-detail',
        'detail',
        'tests/v2/fixtures/scrapers/hirify/detail/response.json',
        'tests/v2/fixtures/scrapers/hirify/detail/meta.json',
        'tests/v2/fixtures/scrapers/hirify/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'jobturbo',
        0,
        'jobturbo-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/jobturbo/success/response.html',
        'tests/v2/fixtures/scrapers/jobturbo/success/meta.json',
        'tests/v2/fixtures/scrapers/jobturbo/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'jobturbo',
        1,
        'jobturbo-no_results',
        'no_results',
        'tests/v2/fixtures/scrapers/jobturbo/no_results/response.html',
        'tests/v2/fixtures/scrapers/jobturbo/no_results/meta.json',
        'tests/v2/fixtures/scrapers/jobturbo/no_results/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'hirehi',
        0,
        'hirehi-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/hirehi/success/response.html',
        'tests/v2/fixtures/scrapers/hirehi/success/meta.json',
        'tests/v2/fixtures/scrapers/hirehi/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'hirehi',
        1,
        'hirehi-no_results',
        'no_results',
        'tests/v2/fixtures/scrapers/hirehi/no_results/response.html',
        'tests/v2/fixtures/scrapers/hirehi/no_results/meta.json',
        'tests/v2/fixtures/scrapers/hirehi/no_results/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'staff_am',
        0,
        'staff_am-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/staff_am/success/response.html',
        'tests/v2/fixtures/scrapers/staff_am/success/meta.json',
        'tests/v2/fixtures/scrapers/staff_am/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'staff_am',
        1,
        'staff_am-no_results',
        'no_results',
        'tests/v2/fixtures/scrapers/staff_am/no_results/response.html',
        'tests/v2/fixtures/scrapers/staff_am/no_results/meta.json',
        'tests/v2/fixtures/scrapers/staff_am/no_results/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'habr_career',
        5,
        'habr_career-detail-sectioned',
        'detail',
        'tests/v2/fixtures/scrapers/habr_career/detail_sectioned/response.html',
        'tests/v2/fixtures/scrapers/habr_career/detail_sectioned/meta.json',
        'tests/v2/fixtures/scrapers/habr_career/detail_sectioned/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:vk',
        11,
        'career:vk-detail',
        'detail',
        'tests/v2/fixtures/scrapers/career_vk/detail/response.html',
        'tests/v2/fixtures/scrapers/career_vk/detail/meta.json',
        'tests/v2/fixtures/scrapers/career_vk/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'talento',
        2,
        'talento-detail',
        'detail',
        'tests/v2/fixtures/scrapers/talento/detail/response.html',
        'tests/v2/fixtures/scrapers/talento/detail/meta.json',
        'tests/v2/fixtures/scrapers/talento/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'finder_work',
        2,
        'finder_work-detail',
        'detail',
        'tests/v2/fixtures/scrapers/finder_work/detail/response.json',
        'tests/v2/fixtures/scrapers/finder_work/detail/meta.json',
        'tests/v2/fixtures/scrapers/finder_work/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'hirehi',
        2,
        'hirehi-detail',
        'detail',
        'tests/v2/fixtures/scrapers/hirehi/detail/response.html',
        'tests/v2/fixtures/scrapers/hirehi/detail/meta.json',
        'tests/v2/fixtures/scrapers/hirehi/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'staff_am',
        2,
        'staff_am-detail',
        'detail',
        'tests/v2/fixtures/scrapers/staff_am/detail/response.html',
        'tests/v2/fixtures/scrapers/staff_am/detail/meta.json',
        'tests/v2/fixtures/scrapers/staff_am/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:ibs',
        0,
        'career:ibs-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_ibs/success/response.html',
        'tests/v2/fixtures/scrapers/career_ibs/success/meta.json',
        'tests/v2/fixtures/scrapers/career_ibs/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:ibs',
        1,
        'career:ibs-pagination',
        'pagination',
        'tests/v2/fixtures/scrapers/career_ibs/pagination/response.html',
        'tests/v2/fixtures/scrapers/career_ibs/pagination/meta.json',
        'tests/v2/fixtures/scrapers/career_ibs/pagination/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:ibs',
        2,
        'career:ibs-pagination-page-3',
        'pagination',
        'tests/v2/fixtures/scrapers/career_ibs/pagination_page_3/response.html',
        'tests/v2/fixtures/scrapers/career_ibs/pagination_page_3/meta.json',
        'tests/v2/fixtures/scrapers/career_ibs/pagination_page_3/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:ibs',
        3,
        'career:ibs-pagination-page-4',
        'pagination',
        'tests/v2/fixtures/scrapers/career_ibs/pagination_page_4/response.html',
        'tests/v2/fixtures/scrapers/career_ibs/pagination_page_4/meta.json',
        'tests/v2/fixtures/scrapers/career_ibs/pagination_page_4/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:ibs',
        4,
        'career:ibs-pagination-page-5',
        'pagination',
        'tests/v2/fixtures/scrapers/career_ibs/pagination_page_5/response.html',
        'tests/v2/fixtures/scrapers/career_ibs/pagination_page_5/meta.json',
        'tests/v2/fixtures/scrapers/career_ibs/pagination_page_5/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:ibs',
        5,
        'career:ibs-pagination-page-6',
        'pagination',
        'tests/v2/fixtures/scrapers/career_ibs/pagination_page_6/response.html',
        'tests/v2/fixtures/scrapers/career_ibs/pagination_page_6/meta.json',
        'tests/v2/fixtures/scrapers/career_ibs/pagination_page_6/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:ibs',
        6,
        'career:ibs-detail',
        'detail',
        'tests/v2/fixtures/scrapers/career_ibs/detail/response.html',
        'tests/v2/fixtures/scrapers/career_ibs/detail/meta.json',
        'tests/v2/fixtures/scrapers/career_ibs/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:amocrm',
        0,
        'career:amocrm-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_amocrm/success/response.html',
        'tests/v2/fixtures/scrapers/career_amocrm/success/meta.json',
        'tests/v2/fixtures/scrapers/career_amocrm/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:amocrm',
        1,
        'career:amocrm-detail',
        'detail',
        'tests/v2/fixtures/scrapers/career_amocrm/detail/response.html',
        'tests/v2/fixtures/scrapers/career_amocrm/detail/meta.json',
        'tests/v2/fixtures/scrapers/career_amocrm/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:amocrm',
        2,
        'career:amocrm-detail-sections',
        'detail',
        'tests/v2/fixtures/scrapers/career_amocrm/detail_sections/response.html',
        'tests/v2/fixtures/scrapers/career_amocrm/detail_sections/meta.json',
        'tests/v2/fixtures/scrapers/career_amocrm/detail_sections/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:coinspaid',
        0,
        'career:coinspaid-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_coinspaid/success/response.json',
        'tests/v2/fixtures/scrapers/career_coinspaid/success/meta.json',
        'tests/v2/fixtures/scrapers/career_coinspaid/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:appfollow',
        0,
        'career:appfollow-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_appfollow/success/response.json',
        'tests/v2/fixtures/scrapers/career_appfollow/success/meta.json',
        'tests/v2/fixtures/scrapers/career_appfollow/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:appfollow',
        1,
        'career:appfollow-detail',
        'detail',
        'tests/v2/fixtures/scrapers/career_appfollow/detail/response.html',
        'tests/v2/fixtures/scrapers/career_appfollow/detail/meta.json',
        'tests/v2/fixtures/scrapers/career_appfollow/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:appfollow',
        2,
        'career:appfollow-detail-backend',
        'detail',
        'tests/v2/fixtures/scrapers/career_appfollow/detail_backend/response.html',
        'tests/v2/fixtures/scrapers/career_appfollow/detail_backend/meta.json',
        'tests/v2/fixtures/scrapers/career_appfollow/detail_backend/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:airslate',
        0,
        'career:airslate-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_airslate/success/response.json',
        'tests/v2/fixtures/scrapers/career_airslate/success/meta.json',
        'tests/v2/fixtures/scrapers/career_airslate/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:wintermute',
        0,
        'career:wintermute-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_wintermute/success/response.json',
        'tests/v2/fixtures/scrapers/career_wintermute/success/meta.json',
        'tests/v2/fixtures/scrapers/career_wintermute/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:truv',
        0,
        'career:truv-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_truv/success/response.json',
        'tests/v2/fixtures/scrapers/career_truv/success/meta.json',
        'tests/v2/fixtures/scrapers/career_truv/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:termius',
        0,
        'career:termius-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_termius/success/response.json',
        'tests/v2/fixtures/scrapers/career_termius/success/meta.json',
        'tests/v2/fixtures/scrapers/career_termius/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:outschool',
        0,
        'career:outschool-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_outschool/success/response.json',
        'tests/v2/fixtures/scrapers/career_outschool/success/meta.json',
        'tests/v2/fixtures/scrapers/career_outschool/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:zeroavia',
        0,
        'career:zeroavia-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_zeroavia/success/response.html',
        'tests/v2/fixtures/scrapers/career_zeroavia/success/meta.json',
        'tests/v2/fixtures/scrapers/career_zeroavia/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:wallarm',
        0,
        'career:wallarm-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_wallarm/success/response.json',
        'tests/v2/fixtures/scrapers/career_wallarm/success/meta.json',
        'tests/v2/fixtures/scrapers/career_wallarm/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:chainstack',
        0,
        'career:chainstack-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_chainstack/success/response.json',
        'tests/v2/fixtures/scrapers/career_chainstack/success/meta.json',
        'tests/v2/fixtures/scrapers/career_chainstack/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:3commas',
        0,
        'career:3commas-success',
        'success_non_empty',
        'tests/v2/fixtures/scrapers/career_3commas/success/response.json',
        'tests/v2/fixtures/scrapers/career_3commas/success/meta.json',
        'tests/v2/fixtures/scrapers/career_3commas/success/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    );

WITH ats_company_success_fixtures(source_id, folder) AS (
    VALUES
        ('career:collectly', 'career_collectly'),
        ('career:planner5d', 'career_planner5d'),
        ('career:superannotate', 'career_superannotate'),
        ('career:xsolla', 'career_xsolla'),
        ('career:unlimint', 'career_unlimint'),
        ('career:clickhouse', 'career_clickhouse'),
        ('career:datafold', 'career_datafold'),
        ('career:inworld', 'career_inworld'),
        ('career:luminai', 'career_luminai'),
        ('career:teleport', 'career_teleport'),
        ('career:mapbox', 'career_mapbox'),
        ('career:joom', 'career_joom'),
        ('career:zeptolab', 'career_zeptolab'),
        ('career:abbyy', 'career_abbyy'),
        ('career:ahrefs', 'career_ahrefs'),
        ('career:eqvilent', 'career_eqvilent'),
        ('career:humansignal', 'career_humansignal'),
        ('career:lokalise', 'career_lokalise'),
        ('career:flo-health', 'career_flo-health'),
        ('career:pandadoc', 'career_pandadoc'),
        ('career:wrike', 'career_wrike'),
        ('career:adtech-holding', 'career_adtech-holding'),
        ('career:altenar', 'career_altenar'),
        ('career:synder', 'career_synder'),
        ('career:onemarketdata', 'career_onemarketdata'),
        ('career:crystal', 'career_crystal'),
        ('career:synthesized', 'career_synthesized'),
        ('career:tradingview', 'career_tradingview'),
        ('career:osome', 'career_osome'),
        ('career:sumsub', 'career_sumsub'),
        ('career:semrush', 'career_semrush'),
        ('career:quadcode', 'career_quadcode'),
        ('career:bunq', 'career_bunq')
)
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
SELECT
    source_id,
    0,
    source_id || '-success',
    'success_non_empty',
    'tests/v2/fixtures/scrapers/' || folder || '/success/response.json',
    'tests/v2/fixtures/scrapers/' || folder || '/success/meta.json',
    'tests/v2/fixtures/scrapers/' || folder || '/success/expected.raw.json',
    1,
    'codex_direct_fixture_review'
FROM ats_company_success_fixtures
WHERE source_id NOT IN (
    'career:joom',
    'career:zeptolab',
    'career:homebuddy',
    'career:lyka',
    'career:thesoul-publishing',
    'career:crystal',
    'career:synthesized',
    'career:tradingview',
    'career:osome',
    'career:sumsub'
);

WITH ats_company_success_html_fixtures(source_id, folder) AS (
    VALUES
        ('career:joom', 'career_joom'),
        ('career:zeptolab', 'career_zeptolab'),
        ('career:homebuddy', 'career_homebuddy'),
        ('career:lyka', 'career_lyka'),
        ('career:thesoul-publishing', 'career_thesoul-publishing'),
        ('career:crystal', 'career_crystal'),
        ('career:synthesized', 'career_synthesized'),
        ('career:tradingview', 'career_tradingview'),
        ('career:osome', 'career_osome'),
        ('career:sumsub', 'career_sumsub'),
        ('career:vivid-money', 'career_vivid-money'),
        ('career:sidestream', 'career_sidestream'),
        ('career:sbk-parus', 'career_sbk-parus'),
        ('career:softmall', 'career_softmall'),
        ('career:retnnet', 'career_retnnet'),
        ('career:znanie', 'career_znanie'),
        ('career:nii-spetsvuzavtomatika', 'career_nii-spetsvuzavtomatika'),
        ('career:social-discovery-group', 'career_social-discovery-group'),
        ('career:prequel', 'career_prequel'),
        ('career:veryfi', 'career_veryfi'),
        ('career:switchboard', 'career_switchboard'),
        ('career:apicworld', 'career_apicworld'),
        ('career:themis-insight', 'career_themis-insight')
)
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
SELECT
    source_id,
    0,
    source_id || '-success',
    'success_non_empty',
    'tests/v2/fixtures/scrapers/' || folder || '/success/response.html',
    'tests/v2/fixtures/scrapers/' || folder || '/success/meta.json',
    'tests/v2/fixtures/scrapers/' || folder || '/success/expected.raw.json',
    1,
    'codex_direct_fixture_review'
FROM ats_company_success_html_fixtures;

WITH smartrecruiters_success_fixtures(source_id, folder) AS (
    VALUES
        ('career:smartrecruiters', 'career_smartrecruiters'),
        ('career:bosch', 'career_bosch'),
        ('career:visa', 'career_visa')
)
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
SELECT
    source_id,
    0,
    source_id || '-success',
    'success_non_empty',
    'tests/v2/fixtures/scrapers/' || folder || '/success/response.json',
    'tests/v2/fixtures/scrapers/' || folder || '/success/meta.json',
    'tests/v2/fixtures/scrapers/' || folder || '/success/expected.raw.json',
    1,
    'codex_direct_fixture_review'
FROM smartrecruiters_success_fixtures;

WITH comeet_success_fixtures(source_id, folder) AS (
    VALUES
        ('career:tripleten', 'career_tripleten'),
        ('career:comm-it', 'career_comm-it')
)
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
SELECT
    source_id,
    0,
    source_id || '-success',
    'success_non_empty',
    'tests/v2/fixtures/scrapers/' || folder || '/success/response.html',
    'tests/v2/fixtures/scrapers/' || folder || '/success/meta.json',
    'tests/v2/fixtures/scrapers/' || folder || '/success/expected.raw.json',
    1,
    'codex_direct_fixture_review'
FROM comeet_success_fixtures;

WITH jobvite_success_fixtures(source_id, folder) AS (
    VALUES
        ('career:progress', 'career_progress'),
        ('career:visionist', 'career_visionist')
)
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
SELECT
    source_id,
    0,
    source_id || '-success',
    'success_non_empty',
    'tests/v2/fixtures/scrapers/' || folder || '/success/response.html',
    'tests/v2/fixtures/scrapers/' || folder || '/success/meta.json',
    'tests/v2/fixtures/scrapers/' || folder || '/success/expected.raw.json',
    1,
    'codex_direct_fixture_review'
FROM jobvite_success_fixtures;

WITH jazzhr_success_fixtures(source_id, folder) AS (
    VALUES
        ('career:foundation-ai', 'career_foundation-ai'),
        ('career:imanage', 'career_imanage'),
        ('career:pairsoft', 'career_pairsoft')
)
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
SELECT
    source_id,
    0,
    source_id || '-success',
    'success_non_empty',
    'tests/v2/fixtures/scrapers/' || folder || '/success/response.html',
    'tests/v2/fixtures/scrapers/' || folder || '/success/meta.json',
    'tests/v2/fixtures/scrapers/' || folder || '/success/expected.raw.json',
    1,
    'codex_direct_fixture_review'
FROM jazzhr_success_fixtures;

WITH icims_success_fixtures(source_id, folder) AS (
    VALUES
        ('career:expleo', 'career_expleo'),
        ('career:epe-consulting', 'career_epe-consulting'),
        ('career:western-southern', 'career_western-southern')
)
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
SELECT
    source_id,
    0,
    source_id || '-success',
    'success_non_empty',
    'tests/v2/fixtures/scrapers/' || folder || '/success/response.html',
    'tests/v2/fixtures/scrapers/' || folder || '/success/meta.json',
    'tests/v2/fixtures/scrapers/' || folder || '/success/expected.raw.json',
    1,
    'codex_direct_fixture_review'
FROM icims_success_fixtures;

WITH taleo_success_fixtures(source_id, folder) AS (
    VALUES
        ('career:keylogic', 'career_keylogic'),
        ('career:navstar', 'career_navstar'),
        ('career:aurora-flight-sciences', 'career_aurora-flight-sciences'),
        ('career:mediacom', 'career_mediacom')
)
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
SELECT
    source_id,
    0,
    source_id || '-success',
    'success_non_empty',
    'tests/v2/fixtures/scrapers/' || folder || '/success/response.html',
    'tests/v2/fixtures/scrapers/' || folder || '/success/meta.json',
    'tests/v2/fixtures/scrapers/' || folder || '/success/expected.raw.json',
    1,
    'codex_direct_fixture_review'
FROM taleo_success_fixtures;

WITH successfactors_success_fixtures(source_id, folder) AS (
    VALUES
        ('career:pictet', 'career_pictet'),
        ('career:brevard-county', 'career_brevard-county'),
        ('career:mindray', 'career_mindray')
)
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
SELECT
    source_id,
    0,
    source_id || '-success',
    'success_non_empty',
    'tests/v2/fixtures/scrapers/' || folder || '/success/response.html',
    'tests/v2/fixtures/scrapers/' || folder || '/success/meta.json',
    'tests/v2/fixtures/scrapers/' || folder || '/success/expected.raw.json',
    1,
    'codex_direct_fixture_review'
FROM successfactors_success_fixtures;

WITH ats_company_pagination_fixtures(source_id, folder) AS (
    VALUES
        ('career:tradingview', 'career_tradingview'),
        ('career:osome', 'career_osome'),
        ('career:sumsub', 'career_sumsub'),
        ('career:nii-spetsvuzavtomatika', 'career_nii-spetsvuzavtomatika')
)
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
SELECT
    source_id,
    1,
    source_id || '-pagination',
    'pagination',
    'tests/v2/fixtures/scrapers/' || folder || '/pagination/response.html',
    'tests/v2/fixtures/scrapers/' || folder || '/pagination/meta.json',
    'tests/v2/fixtures/scrapers/' || folder || '/pagination/expected.raw.json',
    1,
    'codex_direct_fixture_review'
FROM ats_company_pagination_fixtures;

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
VALUES (
    'career:mediacom',
    1,
    'career:mediacom-pagination-terminal',
    'pagination',
    'tests/v2/fixtures/scrapers/career_mediacom/pagination_terminal/response.html',
    'tests/v2/fixtures/scrapers/career_mediacom/pagination_terminal/meta.json',
    'tests/v2/fixtures/scrapers/career_mediacom/pagination_terminal/expected.raw.json',
    1,
    'codex_direct_fixture_review'
);

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
        'career:visionist',
        1,
        'career:visionist-pagination-software-engineering',
        'pagination',
        'tests/v2/fixtures/scrapers/career_visionist/pagination_software_engineering/response.html',
        'tests/v2/fixtures/scrapers/career_visionist/pagination_software_engineering/meta.json',
        'tests/v2/fixtures/scrapers/career_visionist/pagination_software_engineering/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:epe-consulting',
        1,
        'career:epe-consulting-pagination-pr-1',
        'pagination',
        'tests/v2/fixtures/scrapers/career_epe-consulting/pagination_pr_1/response.html',
        'tests/v2/fixtures/scrapers/career_epe-consulting/pagination_pr_1/meta.json',
        'tests/v2/fixtures/scrapers/career_epe-consulting/pagination_pr_1/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:western-southern',
        1,
        'career:western-southern-pagination-pr-1',
        'pagination',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_1/response.html',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_1/meta.json',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_1/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:western-southern',
        2,
        'career:western-southern-pagination-pr-2',
        'pagination',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_2/response.html',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_2/meta.json',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_2/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:western-southern',
        3,
        'career:western-southern-pagination-pr-3',
        'pagination',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_3/response.html',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_3/meta.json',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_3/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:western-southern',
        4,
        'career:western-southern-pagination-pr-4',
        'pagination',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_4/response.html',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_4/meta.json',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_4/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:western-southern',
        5,
        'career:western-southern-pagination-pr-5',
        'pagination',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_5/response.html',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_5/meta.json',
        'tests/v2/fixtures/scrapers/career_western-southern/pagination_pr_5/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:keylogic',
        1,
        'career:keylogic-pagination-row-10',
        'pagination',
        'tests/v2/fixtures/scrapers/career_keylogic/pagination_row_10/response.html',
        'tests/v2/fixtures/scrapers/career_keylogic/pagination_row_10/meta.json',
        'tests/v2/fixtures/scrapers/career_keylogic/pagination_row_10/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:keylogic',
        2,
        'career:keylogic-pagination-row-20',
        'pagination',
        'tests/v2/fixtures/scrapers/career_keylogic/pagination_row_20/response.html',
        'tests/v2/fixtures/scrapers/career_keylogic/pagination_row_20/meta.json',
        'tests/v2/fixtures/scrapers/career_keylogic/pagination_row_20/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:navstar',
        1,
        'career:navstar-pagination-row-10',
        'pagination',
        'tests/v2/fixtures/scrapers/career_navstar/pagination_row_10/response.html',
        'tests/v2/fixtures/scrapers/career_navstar/pagination_row_10/meta.json',
        'tests/v2/fixtures/scrapers/career_navstar/pagination_row_10/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:navstar',
        2,
        'career:navstar-pagination-row-20',
        'pagination',
        'tests/v2/fixtures/scrapers/career_navstar/pagination_row_20/response.html',
        'tests/v2/fixtures/scrapers/career_navstar/pagination_row_20/meta.json',
        'tests/v2/fixtures/scrapers/career_navstar/pagination_row_20/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:navstar',
        3,
        'career:navstar-pagination-row-30',
        'pagination',
        'tests/v2/fixtures/scrapers/career_navstar/pagination_row_30/response.html',
        'tests/v2/fixtures/scrapers/career_navstar/pagination_row_30/meta.json',
        'tests/v2/fixtures/scrapers/career_navstar/pagination_row_30/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:aurora-flight-sciences',
        1,
        'career:aurora-flight-sciences-pagination-row-10',
        'pagination',
        'tests/v2/fixtures/scrapers/career_aurora-flight-sciences/pagination_row_10/response.html',
        'tests/v2/fixtures/scrapers/career_aurora-flight-sciences/pagination_row_10/meta.json',
        'tests/v2/fixtures/scrapers/career_aurora-flight-sciences/pagination_row_10/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    );

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
        'career:semrush',
        3,
        'career:semrush-detail',
        'detail',
        'tests/v2/fixtures/scrapers/career_semrush/detail/response.json',
        'tests/v2/fixtures/scrapers/career_semrush/detail/meta.json',
        'tests/v2/fixtures/scrapers/career_semrush/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:vivid-money',
        1,
        'career:vivid-money-detail',
        'detail',
        'tests/v2/fixtures/scrapers/career_vivid-money/detail/response.html',
        'tests/v2/fixtures/scrapers/career_vivid-money/detail/meta.json',
        'tests/v2/fixtures/scrapers/career_vivid-money/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:sidestream',
        1,
        'career:sidestream-detail',
        'detail',
        'tests/v2/fixtures/scrapers/career_sidestream/detail/response.html',
        'tests/v2/fixtures/scrapers/career_sidestream/detail/meta.json',
        'tests/v2/fixtures/scrapers/career_sidestream/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:sbk-parus',
        1,
        'career:sbk-parus-detail',
        'detail',
        'tests/v2/fixtures/scrapers/career_sbk-parus/detail/response.html',
        'tests/v2/fixtures/scrapers/career_sbk-parus/detail/meta.json',
        'tests/v2/fixtures/scrapers/career_sbk-parus/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:softmall',
        1,
        'career:softmall-detail',
        'detail',
        'tests/v2/fixtures/scrapers/career_softmall/detail/response.html',
        'tests/v2/fixtures/scrapers/career_softmall/detail/meta.json',
        'tests/v2/fixtures/scrapers/career_softmall/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:retnnet',
        1,
        'career:retnnet-detail',
        'detail',
        'tests/v2/fixtures/scrapers/career_retnnet/detail/response.html',
        'tests/v2/fixtures/scrapers/career_retnnet/detail/meta.json',
        'tests/v2/fixtures/scrapers/career_retnnet/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:znanie',
        1,
        'career:znanie-detail',
        'detail',
        'tests/v2/fixtures/scrapers/career_znanie/detail/response.html',
        'tests/v2/fixtures/scrapers/career_znanie/detail/meta.json',
        'tests/v2/fixtures/scrapers/career_znanie/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:nii-spetsvuzavtomatika',
        2,
        'career:nii-spetsvuzavtomatika-detail',
        'detail',
        'tests/v2/fixtures/scrapers/career_nii-spetsvuzavtomatika/detail/response.html',
        'tests/v2/fixtures/scrapers/career_nii-spetsvuzavtomatika/detail/meta.json',
        'tests/v2/fixtures/scrapers/career_nii-spetsvuzavtomatika/detail/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    );

UPDATE sources
SET identity_namespace = 'talento-network'
WHERE source_id IN ('talanto', 'talento');

INSERT INTO source_criteria (source_id, criterion_order, criterion, capability)
SELECT source_id, 8, 'employer_geographies', 'unsupported'
FROM sources;

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
        'career:semrush',
        1,
        'career:semrush-pagination-offset-20',
        'pagination',
        'tests/v2/fixtures/scrapers/career_semrush/pagination_offset_20/response.json',
        'tests/v2/fixtures/scrapers/career_semrush/pagination_offset_20/meta.json',
        'tests/v2/fixtures/scrapers/career_semrush/pagination_offset_20/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    ),
    (
        'career:semrush',
        2,
        'career:semrush-pagination-offset-40',
        'pagination',
        'tests/v2/fixtures/scrapers/career_semrush/pagination_offset_40/response.json',
        'tests/v2/fixtures/scrapers/career_semrush/pagination_offset_40/meta.json',
        'tests/v2/fixtures/scrapers/career_semrush/pagination_offset_40/expected.raw.json',
        1,
        'codex_direct_fixture_review'
    );
