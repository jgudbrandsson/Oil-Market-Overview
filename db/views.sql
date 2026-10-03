-- Derived signals, computed on read. Views hold no data, so dropping and
-- recreating them on every init is safe.

DROP VIEW IF EXISTS v_brent_wti;
CREATE VIEW v_brent_wti AS
SELECT w.obs_date,
       w.value           AS wti,
       b.value           AS brent,
       b.value - w.value AS spread
FROM observations w
JOIN observations b ON b.obs_date = w.obs_date AND b.series_key = 'brent_spot'
WHERE w.series_key = 'wti_spot';

-- 3-2-1 crack in $/bbl: (2 x gasoline + 1 x diesel - 3 x crude) / 3.
-- Product prices are $/gal, so x 42 converts them to $/bbl.
DROP VIEW IF EXISTS v_crack_321;
CREATE VIEW v_crack_321 AS
SELECT w.obs_date,
       w.value AS wti,
       g.value AS gasoline_gal,
       d.value AS ulsd_gal,
       (2 * g.value * 42 + d.value * 42 - 3 * w.value) / 3 AS crack
FROM observations w
JOIN observations g ON g.obs_date = w.obs_date AND g.series_key = 'gasoline_nyh'
JOIN observations d ON d.obs_date = w.obs_date AND d.series_key = 'ulsd_nyh'
WHERE w.series_key = 'wti_spot';
