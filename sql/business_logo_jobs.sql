-- Business-page logo uploads (OLX UZ), tied to Jobs category and premium status.
--
-- WHY EVENTS: there is no current-state logo table for UZ in yamato.
--   * livesync.cee_olxuz_users_business has NO logo column (name/description/phones only);
--     premium_expires_at there is the "has business page access" flag.
--   * livesync.cee_olx<cc>_users_logo exists only for PL; the UZ Spectrum CDC table
--     spectrum_livesync_history.cee_olxuz_users_logo has partitions (2022-05..2024-05)
--     but ZERO rows — files purged / never landed.
--   * product_analytics_odyn.dim_business_logo_history covers PL only.
-- So logo activity comes from Ninja tracking (hydra.web). Logo upload happens in
-- web settings only — hydra.android / hydra.ios carry no *_logo_add events.
--
-- LIMIT: hydra UZ retention starts 2025-07-02 — "ever uploaded" really means
-- "uploaded since then". For the true current stock of logos, query the reservoir
-- via Presto on the colibri server (pattern: nestor_grabets/goods/
-- single_premium_logo_banner_ro.ipynb, table livesync.cee_olx<cc>_users_logo,
-- riak_key is not null and riak_mapping='1').

with logo_uploaders as (
    select user_id,
           min(server_date_day) as first_upload,
           max(server_date_day) as last_upload
    from hydra.web
    where country_code = 'UZ'
      and eventname in ('my_olx_settings_logo_add', 'my_olx_settings_logo_change',
                        'packet_logo_add', 'packet_logo_change')
      and user_id is not null and user_id > 0
    group by 1
),
active_jobs_listers as (
    select distinct a.user_id
    from livesync.cee_olxuz_ads a
    join eu_bi.dim_categories dc
      on dc.category_nk = a.category_id and dc.site_sk = 'olx|eu|uz'
    where dc.category_l1_taxonomy_id = 'Jobs'
      and a.status = 'active'
),
premium as (  -- users with business page access right now
    select id as user_id
    from livesync.cee_olxuz_users_business
    where premium_expires_at >= current_date
)
select
    (select count(*) from logo_uploaders)                             as uploaders_all,
    (select count(*) from logo_uploaders join active_jobs_listers using (user_id))
                                                                      as uploaders_with_active_jobs_ads,
    (select count(*) from logo_uploaders join premium using (user_id))
                                                                      as uploaders_with_premium_now,
    (select count(*) from logo_uploaders
        join premium using (user_id)
        join active_jobs_listers using (user_id))                     as uploaders_premium_and_jobs_now;

-- Reference results, run 2026-08-13 (window 2025-07-02 .. 2026-08-12):
--   uploaders_all                   5252
--   uploaders_with_active_jobs_ads   287   (1614 if "ever posted a jobs ad")
--   uploaders_with_premium_now       948
--   uploaders_premium_and_jobs_now   151
