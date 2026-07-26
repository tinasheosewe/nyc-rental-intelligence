"""
DataStore — local bulk-data layer for all scoring datasets.

Downloads reference and incident datasets from NYC Open Data (SODA) and
MTA GTFS into local SQLite tables.  Scorers query these tables instead
of hitting remote APIs per-listing.

Architecture
------------
- Each dataset is defined by a ``DatasetDef`` (name, SODA ID, columns to
  keep, refresh cadence, optional SoQL filter).
- On first run (or manual ``--refresh``), data is paginated from SODA and
  inserted into a dedicated SQLite table.
- A ``_data_meta`` table tracks when each dataset was last refreshed.
- In **dev mode** (default), data is never auto-refreshed — only downloaded
  when the table is empty or you explicitly pass ``--force``.
- In **prod mode** (``APTHUNT_ENV=production``), ``refresh_stale()`` checks
  cadences and re-downloads anything overdue.
- Old data is **never deleted before new data lands** — the strategy is
  atomic replace (write to temp table, then swap).

Tables created
--------------
One per dataset, named ``ds_{dataset_name}``.  Plus ``_data_meta`` for
bookkeeping.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from sodapy import Socrata

log = logging.getLogger(__name__)

DOMAIN = "data.cityofnewyork.us"

# ---------------------------------------------------------------------------
# Dataset definitions
# ---------------------------------------------------------------------------

@dataclass
class DatasetDef:
    """Specification for a single downloadable dataset."""
    name: str                          # table will be ds_{name}
    soda_id: str                       # NYC Open Data 4×4 identifier (or "" for non-SODA)
    select: str                        # SoQL $select — columns to keep
    refresh_days: int                  # how often to re-download
    where: str = ""                    # optional SoQL $where filter
    geo_columns: list[str] = field(default_factory=list)  # lat/lon cols to index
    index_columns: list[str] = field(default_factory=list)  # extra cols to index
    page_size: int = 50_000           # rows per SODA request
    post_process: str = ""            # optional post-processing hook name
    source: str = "soda"              # "soda", "overpass", or "overpass_roads"
    domain: str = ""                  # override Socrata domain (default DOMAIN)
    real_columns: list[str] = field(default_factory=list)  # numeric cols typed REAL


# All datasets we bulk-download
DATASETS: dict[str, DatasetDef] = {

    # ── Reference (small, change slowly) ─────────────────────────

    "parks": DatasetDef(
        name="parks",
        soda_id="enfh-gkve",
        select="name311, multipolygon",
        refresh_days=90,          # quarterly
        post_process="parks_centroid",  # compute centroid lat/lon from geometry
    ),

    "schools": DatasetDef(
        name="schools",
        soda_id="97mf-9njv",
        select="school_name, latitude, longitude, attendance_rate, pct_stu_safe",
        refresh_days=180,         # biannually (data refreshes each school year)
        geo_columns=["latitude", "longitude"],
    ),

    "pluto": DatasetDef(
        name="pluto",
        soda_id="64uk-42ks",
        select="bbl,address,yearbuilt,numfloors,unitsres,"
               "firm07_flag,pfirm15_flag,latitude,longitude,ownername,cd",
        refresh_days=180,         # biannual MapPLUTO releases
        geo_columns=["latitude", "longitude"],
        index_columns=["ownername"],
    ),

    # ── Incident (large, change frequently) ──────────────────────

    # NYPD complaints arrive as TWO upstream datasets: the historic feed
    # (complete but ~2 quarters behind) and the current-year YTD feed
    # (fresh but resets every January). Consumers should never care: both
    # sources merge into a single derived ``ds_crime`` table at load time
    # (see DERIVED_DATASETS), and scorers just use the date column.
    "crime_historic": DatasetDef(
        name="crime_historic",
        soda_id="qgea-i56i",
        select="cmplnt_num,cmplnt_fr_dt,law_cat_cd,latitude,longitude",
        refresh_days=30,          # historic dataset updates quarterly
        where="cmplnt_fr_dt > '{TWO_YEARS_AGO}' AND latitude IS NOT NULL",
        geo_columns=["latitude", "longitude"],
        post_process="rebuild_crime_merged",
    ),

    # NB: dataset also serves PestScorer (Rodent) and ManagementScorer
    # (HEAT/HOT WATER).  Name kept as "noise" for backward compat.
    "noise": DatasetDef(
        name="noise",
        soda_id="erm2-nwe9",
        select="unique_key,created_date,complaint_type,latitude,longitude",
        refresh_days=7,           # weekly
        where=(
            "complaint_type IN ("
            "'Noise - Residential','Noise - Street/Sidewalk',"
            "'Noise - Commercial','Noise - Vehicle','Noise - Park',"
            "'Rodent','HEAT/HOT WATER'"
            ") AND created_date > '{TWELVE_MONTHS_AGO}'"
        ),
        geo_columns=["latitude", "longitude"],
    ),

    "dob_violations": DatasetDef(
        name="dob_violations",
        soda_id="3h2n-5cm9",
        select="isn_dob_bis_viol,boro,block,lot,"
               "violation_type,violation_category,issue_date",
        refresh_days=30,          # monthly
        where="violation_category NOT LIKE '%Resolved%'",
    ),

    "hpd_complaints": DatasetDef(
        name="hpd_complaints",
        soda_id="ygpa-z7cr",
        select="complaint_id,bbl,received_date,major_category,"
               "minor_category,complaint_status",
        refresh_days=7,           # weekly
        where="received_date > '{TWELVE_MONTHS_AGO}'",
        index_columns=["bbl"],
    ),

    "amenities": DatasetDef(
        name="amenities",
        soda_id="",                # not a SODA dataset
        select="",
        refresh_days=90,          # quarterly — OSM changes slowly
        geo_columns=["lat", "lon"],
        source="overpass",
    ),

    # ── Road network (traffic exposure) ──────────────────────────

    # OSM major-road ways (motorway → tertiary), each polyline sampled
    # every ~50 m into point rows so scorers can use plain bbox/circle
    # queries. Table: ds_roads(way_id, road_class, name, lanes, lat, lon).
    "roads": DatasetDef(
        name="roads",
        soda_id="",                # not SODA — OSM via Overpass
        select="",
        refresh_days=180,          # biannual — the road network barely changes
        geo_columns=["lat", "lon"],
        source="overpass_roads",
    ),

    # NYC DOT designated truck routes (line geometry in the_geom).
    # Post-processed into ~50 m sampled point rows (route_type, lat, lon)
    # in the same table; original line rows are dropped.
    "truck_routes": DatasetDef(
        name="truck_routes",
        soda_id="jjja-shxy",       # verified live 2026-07 ($limit=1 probe)
        select="street,routetype,truckroute,the_geom",
        refresh_days=180,          # truck-route designations change slowly
        geo_columns=["lat", "lon"],  # columns created by sample_truck_routes
        post_process="sample_truck_routes",
    ),

    "shelters": DatasetDef(
        name="shelters",
        soda_id="ji82-xba5",      # NYC Facilities Database
        select="facname,factype,facsubgrp,address,boro,latitude,longitude",
        refresh_days=180,         # biannual — facilities change slowly
        where="facsubgrp = 'NON-RESIDENTIAL HOUSING AND HOMELESS SERVICES'"
               " AND latitude IS NOT NULL",
        geo_columns=["latitude", "longitude"],
    ),

    "projects": DatasetDef(
        name="projects",
        soda_id="3ub5-4ph8",     # NYCHA BBL Extract — public housing buildings
        select="development,address,borough,bin,latitude,longitude",
        refresh_days=180,         # biannual — NYCHA changes slowly
        where="latitude IS NOT NULL",
        geo_columns=["latitude", "longitude"],
    ),

    # ── New enrichment datasets ──────────────────────────────────

    "hpd_violations": DatasetDef(
        name="hpd_violations",
        soda_id="wvxf-dwi5",     # HPD Violations — inspector-confirmed issues
        select="violationid,boroid,block,lot,class,"
               "inspectiondate,currentstatus,violationstatus,"
               "novdescription,latitude,longitude",
        refresh_days=7,           # weekly — active violations change often
        where="violationstatus = 'Open' AND inspectiondate > '{TWO_YEARS_AGO}'",
        geo_columns=["latitude", "longitude"],
    ),

    "hpd_litigations": DatasetDef(
        name="hpd_litigations",
        soda_id="59kj-x8nc",     # HPD Litigations — HPD suing landlords
        select="litigationid,boroid,block,lot,"
               "casetype,casestatus,caseopendate,"
               "respondent,latitude,longitude",
        refresh_days=30,          # monthly
        where="casestatus IN ('PENDING','APPLICATION PENDING')",
        geo_columns=["latitude", "longitude"],
    ),

    "evictions": DatasetDef(
        name="evictions",
        soda_id="6z8x-wfk4",     # NYC Marshals — executed evictions
        select="court_index_number,eviction_address,"
               "executed_date,residential_commercial_ind,"
               "borough,latitude,longitude,bbl",
        refresh_days=30,          # monthly
        where="residential_commercial_ind = 'Residential'"
               " AND executed_date > '{TWELVE_MONTHS_AGO}'"
               " AND latitude IS NOT NULL",
        geo_columns=["latitude", "longitude"],
        index_columns=["bbl"],
    ),

    "dob_permits": DatasetDef(
        name="dob_permits",
        soda_id="ic3t-wcy2",     # DOB Job Applications — active permits
        select="job__,borough,block,lot,job_type,"
               "job_status_descrp,gis_latitude,gis_longitude",
        refresh_days=30,          # monthly
        where="job_status_descrp IN ('PERMIT ISSUED - ENTIRE JOB/WORK',"
               "'PERMIT ISSUED - PARTIAL JOB/WORK',"
               "'APPROVED','PARTIALLY APPROVED')"
               " AND gis_latitude IS NOT NULL",
        geo_columns=["gis_latitude", "gis_longitude"],
    ),

    "street_trees": DatasetDef(
        name="street_trees",
        soda_id="uvpi-gqnh",     # 2015 Street Tree Census
        select="tree_id,status,health,spc_common,"
               "tree_dbh,latitude,longitude",
        refresh_days=365,         # annual — census data is static
        where="status = 'Alive' AND latitude IS NOT NULL",
        geo_columns=["latitude", "longitude"],
    ),

    "community_gardens": DatasetDef(
        name="community_gardens",
        soda_id="ajxm-kzmj",     # GreenThumb Community Gardens
        select="garden_name,address,latitude,longitude,size",
        refresh_days=180,         # biannual — gardens change slowly
        where="latitude IS NOT NULL",
        geo_columns=["latitude", "longitude"],
    ),

    "crime_ytd": DatasetDef(
        name="crime_ytd",
        soda_id="5uac-w243",
        select="cmplnt_num,cmplnt_fr_dt,law_cat_cd,latitude,longitude",
        refresh_days=7,
        where="latitude IS NOT NULL",
        geo_columns=["latitude", "longitude"],
        post_process="rebuild_crime_merged",
    ),

    # ── Building forensics (openigloo superset) ──────────────────

    "bedbug_reporting": DatasetDef(
        name="bedbug_reporting",
        soda_id="wz6d-d3jb",     # HPD annual owner bedbug filings
        select="building_id,registration_id,bbl,bin,house_number,street_name,"
               "postcode,of_dwelling_units,infested_dwelling_unit_count,"
               "eradicated_unit_count,re_infested_dwelling_unit,filing_date,"
               "filing_period_start_date,filling_period_end_date,"  # (sic) API typo
               "latitude,longitude",
        refresh_days=30,
        geo_columns=["latitude", "longitude"],
        index_columns=["bbl"],
        real_columns=["of_dwelling_units", "infested_dwelling_unit_count",
                      "eradicated_unit_count", "re_infested_dwelling_unit"],
    ),

    "rodent_inspections": DatasetDef(
        name="rodent_inspections",
        soda_id="p937-wjvj",     # DOHMH rat inspection results
        select="inspection_type,job_id,bbl,boro_code,block,lot,house_number,"
               "street_name,zip_code,latitude,longitude,inspection_date,result,"
               "approved_date,bin",
        refresh_days=14,
        where="inspection_date >= '{TWO_YEARS_AGO}'",
        geo_columns=["latitude", "longitude"],
        index_columns=["bbl"],
        post_process="null_zero_latlon",  # 0.0 coords → NULL (fall back to BBL)
    ),

    "hpd_registrations": DatasetDef(
        name="hpd_registrations",
        soda_id="tesw-yqqr",     # landlord-identity backbone
        select="registrationid,buildingid,boroid,boro,housenumber,lowhousenumber,"
               "highhousenumber,streetname,zip,block,lot,bin,"
               "lastregistrationdate,registrationenddate",
        refresh_days=30,
        index_columns=["registrationid", "bin"],
        post_process="bbl_from_boroid_block_lot",
    ),

    "hpd_registration_contacts": DatasetDef(
        name="hpd_registration_contacts",
        soda_id="feu5-w2e2",     # who owns/manages: officers, agents, corps
        select="registrationcontactid,registrationid,type,contactdescription,"
               "corporationname,title,firstname,middleinitial,lastname,"
               "businesshousenumber,businessstreetname,businessapartment,"
               "businesscity,businessstate,businesszip",
        refresh_days=30,
        index_columns=["registrationid", "corporationname"],
    ),

    "vacate_orders": DatasetDef(
        name="vacate_orders",
        soda_id="tb8q-a3ar",     # HPD vacate orders — most severe red flag
        select="building_id,registration_id,boro_short_name,house_number,"
               "street_name,vacate_order_number,primary_vacate_reason,"
               "vacate_type,vacate_effective_date,actual_rescind_date,"
               "number_of_vacated_units,latitude,longitude,bin,bbl",
        refresh_days=14,
        geo_columns=["latitude", "longitude"],
        index_columns=["bbl"],
    ),

    "aep_buildings": DatasetDef(
        name="aep_buildings",
        soda_id="hcir-3275",     # city-designated worst buildings
        select="building_id,boro,phn,street_address,total_units,aep_start_date,"
               "of_b_c_violations_at_start,current_status,discharge_date,"
               "aep_round,postcode,latitude,longitude,bin,bbl",
        refresh_days=30,
        geo_columns=["latitude", "longitude"],
        index_columns=["bbl"],
    ),

    "ecb_violations": DatasetDef(
        name="ecb_violations",
        soda_id="6bgk-3dad",     # OATH/ECB violations w/ penalties + balance due
        select="ecb_violation_number,ecb_violation_status,dob_violation_number,"
               "bin,boro,block,lot,issue_date,severity,violation_type,"
               "respondent_name,penality_imposed,amount_paid,balance_due,"  # (sic)
               "aggravated_level,hearing_status,certification_status",
        refresh_days=14,
        where="issue_date >= '{TWO_YEARS_AGO_YMD}'",  # BIS-era YYYYMMDD strings
        index_columns=["bin"],
        real_columns=["penality_imposed", "amount_paid", "balance_due"],
        post_process="bbl_from_boro_block_lot",
    ),

    "tax_lien_sale": DatasetDef(
        name="tax_lien_sale",
        soda_id="9rz4-mjek",     # DOF lien-sale list (financial distress)
        select="month,cycle,borough,block,lot,tax_class_code,building_class,"
               "house_number,street_name,zip_code,water_debt_only",
        refresh_days=30,
        where="month >= '{TWO_YEARS_AGO}'",
        post_process="bbl_from_borough_block_lot",
    ),

    "elevator_compliance": DatasetDef(
        name="elevator_compliance",
        soda_id="e5aq-a4j2",     # DOB NOW elevator devices
        select="device_number,device_type,device_status,status_date,"
               "periodic_report_year,cat1_report_year,cat1_latest_report_filed,"
               "periodic_latest_inspection,bin,house_number,street_name,block,"
               "lot,zip_code,latitude,longitude,bbl",
        refresh_days=60,
        where="device_status = 'Active'",
        geo_columns=["latitude", "longitude"],
        index_columns=["bbl", "bin"],
    ),

    "omo_charges": DatasetDef(
        name="omo_charges",
        soda_id="mdbu-nrqn",     # HPD emergency-repair work orders billed to owner
        select="omoid,omonumber,buildingid,boro,housenumber,streetname,apartment,"
               "zip,block,lot,lifecycle,worktypegeneral,omostatusreason,"
               "omoawardamount,omocreatedate,isaep,omodescription,"
               "latitude,longitude,bin,bbl",
        refresh_days=30,
        where="omocreatedate >= '{TWO_YEARS_AGO}'",
        geo_columns=["latitude", "longitude"],
        index_columns=["bbl"],
        real_columns=["omoawardamount"],
    ),

    "speculation_watch_list": DatasetDef(
        name="speculation_watch_list",
        soda_id="adax-9mit",     # recently flipped, likely-speculator buys
        select="bbl,boro,block,lot,hnum_lo,hnum_hi,str_name,crfn,grantee,"
               "deed_date,price,cap_rate,borough_cap_rate,postcode,"
               "latitude,longitude,bin",
        refresh_days=90,
        geo_columns=["latitude", "longitude"],
        index_columns=["bbl"],
        real_columns=["price", "cap_rate"],
    ),

    "ahv_permits": DatasetDef(
        name="ahv_permits",
        soda_id="g76y-dcqj",     # after-hours construction variances (noise)
        select="bin,housenumber,streetname,borough,job_number,ahv_permit_number,"
               "ahvpermitstatus,variancetype,reasonforvariance,"
               "variance_start_date_time,variance_end_date_time,residence_200ft,"
               "enclosed_work,demolition,crane_use,latitude,longitude,bbl",
        refresh_days=7,
        where="variance_start_date_time >= '{TWELVE_MONTHS_AGO}'",
        geo_columns=["latitude", "longitude"],
        index_columns=["bbl"],
    ),

    "sidewalk_sheds": DatasetDef(
        name="sidewalk_sheds",
        soda_id="rbx6-tga4",     # DOB NOW active sidewalk-shed permits
        select="job_filing_number,work_permit,filing_reason,house_no,street_name,"
               "borough,bin,block,lot,work_type,approved_date,issued_date,"
               "expired_date,estimated_job_costs,owner_business_name,"
               "permit_status,zip_code,latitude,longitude,bbl",
        refresh_days=14,
        where="work_type = 'Sidewalk Shed'",
        geo_columns=["latitude", "longitude"],
        index_columns=["bbl", "bin"],
    ),

    # ── Safety & environment ─────────────────────────────────────

    "shootings": DatasetDef(
        name="shootings",
        soda_id="5ucz-vwe8",     # NYPD shootings 2006–present
        select="incident_key,occur_date,occur_time,boro,precinct,location_desc,"
               "latitude,longitude",
        refresh_days=30,
        where="occur_date > '{TWO_YEARS_AGO}' AND latitude IS NOT NULL",
        geo_columns=["latitude", "longitude"],
    ),

    "felony_arrests": DatasetDef(
        name="felony_arrests",
        soda_id="uip8-fykc",     # NYPD arrests YTD (felony only)
        select="arrest_key,arrest_date,ofns_desc,law_cat_cd,arrest_boro,"
               "arrest_precinct,latitude,longitude",
        refresh_days=30,
        where="law_cat_cd='F' AND latitude IS NOT NULL",
        geo_columns=["latitude", "longitude"],
    ),

    "street_collisions": DatasetDef(
        name="street_collisions",
        soda_id="h9gi-nx95",     # crashes injuring pedestrians/cyclists
        select="collision_id,crash_date,crash_time,latitude,longitude,"
               "number_of_pedestrians_injured,number_of_pedestrians_killed,"
               "number_of_cyclist_injured,number_of_cyclist_killed,"
               "number_of_persons_injured,number_of_persons_killed",
        refresh_days=7,
        where="crash_date > '{TWELVE_MONTHS_AGO}' AND latitude > 40 AND "
              "(number_of_pedestrians_injured > 0 OR number_of_cyclist_injured > 0 "
              "OR number_of_pedestrians_killed > 0 OR number_of_cyclist_killed > 0)",
        geo_columns=["latitude", "longitude"],
        real_columns=["number_of_pedestrians_injured", "number_of_pedestrians_killed",
                      "number_of_cyclist_injured", "number_of_cyclist_killed"],
    ),

    "street_qol": DatasetDef(
        name="street_qol",
        soda_id="erm2-nwe9",     # 311: street-condition complaint types
        select="unique_key,created_date,complaint_type,descriptor,incident_zip,"
               "latitude,longitude,status",
        refresh_days=7,
        where=(
            "created_date > '{TWELVE_MONTHS_AGO}' AND complaint_type in("
            "'Sewer','UNSANITARY CONDITION','Dirty Condition','Derelict Vehicles',"
            "'Encampment','Outdoor Dining','Air Quality','Water System'"
            ") AND latitude IS NOT NULL"
        ),
        geo_columns=["latitude", "longitude"],
    ),

    "air_quality": DatasetDef(
        name="air_quality",
        soda_id="c3uy-2p5r",     # NYCCAS by community district
        select="unique_id,indicator_id,name,measure,geo_type_name,geo_join_id,"
               "geo_place_name,time_period,start_date,data_value",
        refresh_days=365,
        where="geo_type_name='CD' AND name in('Fine particles (PM 2.5)',"
              "'Nitrogen dioxide (NO2)','Ozone (O3)')",
        index_columns=["geo_join_id"],
        real_columns=["data_value"],
    ),

    "heat_vulnerability": DatasetDef(
        name="heat_vulnerability",
        soda_id="4mhf-duep",     # HVI quintile by zip (ZCTA)
        select="zcta20,hvi",
        refresh_days=365,
        index_columns=["zcta20"],
        real_columns=["hvi"],
    ),

    # ── Transit & lifestyle ──────────────────────────────────────

    "subway_entrances": DatasetDef(
        name="subway_entrances",
        soda_id="i9wp-a4ja",     # exact entrance points (beats centroids)
        domain="data.ny.gov",    # MTA dataset — NY State portal, not NYC
        select="station_id,complex_id,gtfs_stop_id,stop_name,borough,line,"
               "daytime_routes,entrance_type,entry_allowed,exit_allowed,"
               "entrance_latitude,entrance_longitude",
        refresh_days=180,
        where="entry_allowed='YES'",
        geo_columns=["entrance_latitude", "entrance_longitude"],
    ),

    "subway_stations": DatasetDef(
        name="subway_stations",
        soda_id="39hk-dx4f",     # stations with routes + ADA status
        domain="data.ny.gov",    # MTA dataset — not on the NYC portal
        select="gtfs_stop_id,station_id,complex_id,line,stop_name,borough,cbd,"
               "daytime_routes,structure,gtfs_latitude,gtfs_longitude,ada,"
               "ada_northbound,ada_southbound",
        refresh_days=180,
        geo_columns=["gtfs_latitude", "gtfs_longitude"],
    ),

    "bus_stops": DatasetDef(
        name="bus_stops",
        soda_id="2ucp-7wg5",     # stop × route rows; deduped in post-process
        domain="data.ny.gov",    # MTA dataset — NY State portal, not NYC
        select="stop_id,stop_name,route_id,route_short_name,direction,"
               "latitude,longitude",
        refresh_days=90,
        where="in_effect='true'",
        geo_columns=["latitude", "longitude"],
        post_process="dedupe_bus_stops",
    ),

    "supermarkets": DatasetDef(
        name="supermarkets",
        soda_id="9a8c-vfzj",     # NYS retail food stores (NYC counties)
        domain="data.ny.gov",
        select="county,license_number,operation_type,estab_type,entity_name,"
               "dba_name,street_number,street_name,city,zip_code,"
               "square_footage,georeference",
        refresh_days=90,
        where="county in('NEW YORK','KINGS','QUEENS','BRONX','RICHMOND') "
              "AND operation_type='Store'",
        real_columns=["square_footage"],
        post_process="extract_georeference",
    ),

    "liquor_licenses": DatasetDef(
        name="liquor_licenses",
        soda_id="9s3h-dpkz",     # SLA active licenses (bar density = nightlife)
        domain="data.ny.gov",
        select="licensepermitid,premisescounty,class,description,legalname,dba,"
               "actualaddressofpremises,city,zipcode,effectivedate,"
               "expirationdate,georeference",
        refresh_days=60,
        # County values are Title Case in this dataset ('New York', not 'NEW YORK')
        where="premisescounty in('New York','Kings','Queens','Bronx','Richmond')",
        post_process="extract_georeference",
    ),

    # NB: no DCWP laundromat dataset — the w7w3-xahh licenses file only has
    # *industrial* laundries (NYC no longer licenses retail laundromats).
    # Retail laundromats come from OSM via ds_amenities (category 'laundry').

    "school_zones": DatasetDef(
        name="school_zones",
        soda_id="cmjf-yawu",     # elementary attendance zone polygons
        select="dbn,label,esid_no,boro,schooldist,remarks,the_geom",
        refresh_days=365,
    ),

    "restaurant_inspections": DatasetDef(
        name="restaurant_inspections",
        soda_id="43nn-pn8j",     # DOHMH graded inspections (amenity + vermin signal)
        select="camis,dba,boro,zipcode,cuisine_description,inspection_date,"
               "action,violation_code,critical_flag,score,grade,"
               "latitude,longitude",
        refresh_days=30,
        where="inspection_date > '{TWO_YEARS_AGO}' AND latitude IS NOT NULL",
        geo_columns=["latitude", "longitude"],
        real_columns=["score"],
    ),

    "farmers_markets": DatasetDef(
        name="farmers_markets",
        soda_id="8vwk-6iz2",
        select="year,marketname,borough,streetaddress,latitude,longitude,"
               "daysoperation,hoursoperations,accepts_ebt,open_year_round",
        refresh_days=180,
        geo_columns=["latitude", "longitude"],
        post_process="keep_latest_year",
    ),

    "libraries": DatasetDef(
        name="libraries",
        soda_id="feuq-due4",
        select="name,housenum,streetname,city,zip,url,bin,bbl,system,borocode,"
               "the_geom",
        refresh_days=365,
        post_process="extract_the_geom_point",
    ),
}


# ---------------------------------------------------------------------------
# Derived datasets — tables materialized from multiple source downloads.
# Consumers query the derived name; sources are a data-layer detail.
# ---------------------------------------------------------------------------

DERIVED_DATASETS: dict[str, dict] = {
    # ds_crime = historic feed ∪ current-year YTD feed, deduped on
    # cmplnt_num (historic wins). One table, one date column — scorers
    # never know two upstream feeds exist.
    "crime": {"sources": ("crime_historic", "crime_ytd")},
}


# ---------------------------------------------------------------------------
# Metadata table
# ---------------------------------------------------------------------------

_META_DDL = """
CREATE TABLE IF NOT EXISTS _data_meta (
    dataset     TEXT PRIMARY KEY,
    row_count   INTEGER NOT NULL DEFAULT 0,
    refreshed_at TEXT NOT NULL,
    elapsed_sec  REAL NOT NULL DEFAULT 0
)
"""


# ---------------------------------------------------------------------------
# DataStore
# ---------------------------------------------------------------------------

class DataStore:
    """
    Manages bulk-downloaded datasets in SQLite.

    Usage::

        ds = DataStore(conn)
        ds.download("parks")               # first-time or manual refresh
        ds.download("crime", force=True)    # re-download even if fresh
        ds.refresh_stale()                  # prod: refresh anything overdue
        rows = ds.query("pluto",
                        "latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?",
                        (40.70, 40.72, -74.01, -73.99))
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        app_token: Optional[str] = None,
    ):
        self._conn = conn
        # Fall back to the env token so every construction site benefits —
        # anonymous Socrata traffic is aggressively throttled.
        self._app_token = app_token or os.environ.get("SODA_APP_TOKEN")
        self._conn.execute(_META_DDL)
        self._conn.commit()

    # ------------------------------------------------------------------ public

    def download(
        self,
        name: str,
        *,
        force: bool = False,
        quiet: bool = False,
    ) -> dict:
        """
        Download a dataset if missing, stale, or ``force=True``.

        Returns dict with keys: downloaded (bool), rows, elapsed_sec.
        """
        ddef = DATASETS[name]
        table = f"ds_{name}"

        if not force and self._is_fresh(name, ddef.refresh_days):
            if not quiet:
                meta = self._get_meta(name)
                log.info(
                    "%s: fresh (%s rows, refreshed %s) — skipping",
                    name, meta["row_count"], meta["refreshed_at"],
                )
            return {"downloaded": False, "rows": 0, "elapsed_sec": 0}

        # Download into a staging table, then swap atomically
        staging = f"_staging_{name}"
        t0 = time.time()

        if ddef.source == "overpass":
            total = self._overpass_download(staging, quiet=quiet)
        elif ddef.source == "overpass_roads":
            total = self._overpass_roads_download(staging, quiet=quiet)
        else:
            where = ddef.where
            if "{TWELVE_MONTHS_AGO}" in where:
                cutoff = (datetime.now() - timedelta(days=365)).strftime(
                    "%Y-%m-%dT00:00:00"
                )
                where = where.replace("{TWELVE_MONTHS_AGO}", cutoff)
            if "{TWO_YEARS_AGO}" in where:
                cutoff2 = (datetime.now() - timedelta(days=730)).strftime(
                    "%Y-%m-%dT00:00:00"
                )
                where = where.replace("{TWO_YEARS_AGO}", cutoff2)
            if "{TWO_YEARS_AGO_YMD}" in where:
                # BIS-era datasets store dates as YYYYMMDD strings
                cutoff3 = (datetime.now() - timedelta(days=730)).strftime("%Y%m%d")
                where = where.replace("{TWO_YEARS_AGO_YMD}", cutoff3)

            total = self._paginated_download(
                ddef.soda_id,
                staging,
                select=ddef.select,
                where=where,
                page_size=ddef.page_size,
                quiet=quiet,
                domain=ddef.domain or DOMAIN,
                real_columns=set(ddef.geo_columns) | set(ddef.real_columns),
            )
        elapsed = time.time() - t0

        if total == 0:
            # Don't replace existing data with nothing
            self._conn.execute(f"DROP TABLE IF EXISTS [{staging}]")
            self._conn.commit()
            log.warning("%s: download returned 0 rows — keeping old data", name)
            return {"downloaded": False, "rows": 0, "elapsed_sec": elapsed}

        # Atomic swap — run DROP+RENAME inside one explicit transaction so a
        # crash between the two statements can't leave the dataset missing.
        prev_isolation = self._conn.isolation_level
        try:
            self._conn.commit()
            self._conn.isolation_level = None  # manual transaction control
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(f"DROP TABLE IF EXISTS [{table}]")
            self._conn.execute(f"ALTER TABLE [{staging}] RENAME TO [{table}]")
            self._conn.execute("COMMIT")
        except Exception:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        finally:
            self._conn.isolation_level = prev_isolation

        # Post-processing hooks (e.g. compute derived columns)
        if ddef.post_process:
            self._run_post_process(ddef.post_process, table)

        # Build spatial indexes
        self._build_indexes(table, ddef.geo_columns)

        # Build extra indexes (non-geo columns)
        self._build_indexes(table, ddef.index_columns)

        # Update metadata
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO _data_meta "
            "(dataset, row_count, refreshed_at, elapsed_sec) "
            "VALUES (?, ?, ?, ?)",
            (name, total, now, round(elapsed, 1)),
        )
        self._conn.commit()

        if not quiet:
            log.info(
                "%s: downloaded %s rows in %.1fs",
                name, f"{total:,}", elapsed,
            )
        return {"downloaded": True, "rows": total, "elapsed_sec": elapsed}

    def refresh_stale(self, *, force: bool = False, quiet: bool = False) -> dict:
        """
        Download all datasets that are missing or overdue.

        In dev mode, only downloads if the table doesn't exist at all.
        In prod mode (APTHUNT_ENV=production), respects refresh_days.
        """
        # refresh_stale() is always an explicit ask (manage_data --refresh or a
        # prod cron) — honor the cadence in every environment. download() itself
        # skips anything still fresh, so this never re-downloads needlessly.
        # (Previously dev mode skipped any existing table, so datasets could
        # silently stay stale forever.)
        results = {}
        for name in DATASETS:
            results[name] = self.download(name, force=force, quiet=quiet)
        return results

    def ensure_downloaded(self, name: str, *, quiet: bool = False) -> None:
        """Ensure a dataset exists locally; download if not.

        Derived datasets (DERIVED_DATASETS) ensure their sources first and
        materialize the merged table when missing.
        """
        if name in DERIVED_DATASETS:
            for src in DERIVED_DATASETS[name]["sources"]:
                self.ensure_downloaded(src, quiet=quiet)
            if not self._table_exists(f"ds_{name}"):
                self._rebuild_crime_merged()
            return
        table = f"ds_{name}"
        if not self._table_exists(table):
            self.download(name, quiet=quiet)

    def query(
        self,
        dataset: str,
        where_clause: str = "",
        params: tuple = (),
        select: str = "*",
        limit: int = 0,
    ) -> list[dict]:
        """
        Query a local dataset table.

        Args:
            dataset:      dataset name (e.g. "pluto", "crime")
            where_clause: SQL WHERE (without the WHERE keyword)
            params:       bind parameters
            select:       columns
            limit:        max rows (0 = unlimited)
        """
        table = f"ds_{dataset}"
        sql = f"SELECT {select} FROM [{table}]"
        if where_clause:
            sql += f" WHERE {where_clause}"
        if limit:
            sql += f" LIMIT {limit}"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_bbox(
        self,
        dataset: str,
        lat: float,
        lon: float,
        delta: float = 0.0015,
        select: str = "*",
        lat_col: str = "latitude",
        lon_col: str = "longitude",
    ) -> list[dict]:
        """Convenience: query a bounding box around (lat, lon).

        Geo columns are REAL-typed at download time, so the BETWEEN uses the
        index directly. Legacy TEXT-typed tables MUST be migrated first
        (scripts/migrate_geo_types.py) — TEXT-affinity comparison against
        numbers falls back to string ordering, which is wrong for negative
        longitudes.
        """
        where = (
            f"{lat_col} BETWEEN ? AND ? "
            f"AND {lon_col} BETWEEN ? AND ?"
        )
        return self.query(
            dataset,
            where,
            (lat - delta, lat + delta, lon - delta, lon + delta),
            select=select,
        )

    def query_circle(
        self,
        dataset: str,
        lat: float,
        lon: float,
        radius_m: float,
        select: str = "*",
        lat_col: str = "latitude",
        lon_col: str = "longitude",
    ) -> list[dict]:
        """
        Query rows within ``radius_m`` meters of (lat, lon).

        Uses a bbox pre-filter + exact Haversine post-filter.
        """
        # 1° latitude ≈ 111,320 m.  At NYC, 1° longitude ≈ 85,000 m.
        lat_delta = radius_m / 111_320
        lon_delta = radius_m / 85_000

        # Ensure geo columns are always included in SELECT for Haversine
        bbox_select = select
        if select != "*":
            sel_cols = {c.strip() for c in select.split(",")}
            missing = {lat_col, lon_col} - sel_cols
            if missing:
                bbox_select = select + ", " + ", ".join(missing)

        # Pre-filter: bbox
        candidates = self.query_bbox(
            dataset, lat, lon, delta=max(lat_delta, lon_delta),
            select=bbox_select, lat_col=lat_col, lon_col=lon_col,
        )

        # Post-filter: exact Haversine. Each returned row carries its
        # distance in meters under "_dist_m" so callers can apply
        # distance-weighted (kernel) counting instead of hard-radius counts.
        from haversine import haversine as _hav, Unit
        results = []
        for row in candidates:
            try:
                rlat = float(row[lat_col])
                rlon = float(row[lon_col])
            except (KeyError, TypeError, ValueError):
                continue
            d = _hav((lat, lon), (rlat, rlon), unit=Unit.METERS)
            if d <= radius_m:
                row["_dist_m"] = d
                results.append(row)
        return results

    def status(self) -> list[dict]:
        """Return metadata for all datasets."""
        rows = self._conn.execute(
            "SELECT dataset, row_count, refreshed_at, elapsed_sec "
            "FROM _data_meta ORDER BY dataset"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            ddef = DATASETS.get(d["dataset"])
            if ddef:
                d["refresh_days"] = ddef.refresh_days
                d["stale"] = not self._is_fresh(d["dataset"], ddef.refresh_days)
            result.append(d)
        # Add missing datasets
        downloaded = {r["dataset"] for r in result}
        for name, ddef in DATASETS.items():
            if name not in downloaded:
                result.append({
                    "dataset": name,
                    "row_count": 0,
                    "refreshed_at": None,
                    "elapsed_sec": 0,
                    "refresh_days": ddef.refresh_days,
                    "stale": True,
                })
        return sorted(result, key=lambda r: r["dataset"])

    # ---------------------------------------------------------------- private

    def _paginated_download(
        self,
        soda_id: str,
        table: str,
        *,
        select: str,
        where: str,
        page_size: int,
        quiet: bool,
        domain: str = DOMAIN,
        real_columns: set | None = None,
    ) -> int:
        """Download via paginated SODA queries into a staging table."""
        client = Socrata(domain, self._app_token, timeout=300)
        real_columns = real_columns or set()

        # Drop staging table if it exists from a previous failed run
        self._conn.execute(f"DROP TABLE IF EXISTS [{table}]")
        self._conn.commit()

        total = 0
        offset = 0
        table_created = False
        max_retries = 5

        try:
            while True:
                kwargs: dict = {
                    "select": select,
                    "limit": page_size,
                    "offset": offset,
                    "order": ":id",
                }
                if where:
                    kwargs["where"] = where

                # Retry with exponential back-off on transient failures
                for attempt in range(1, max_retries + 1):
                    try:
                        rows = client.get(soda_id, **kwargs)
                        break
                    except Exception as exc:
                        if attempt == max_retries:
                            raise
                        wait = 5 * attempt
                        log.warning(
                            "  SODA request failed (attempt %d/%d): %s — retrying in %ds",
                            attempt, max_retries, exc, wait,
                        )
                        time.sleep(wait)

                if not rows:
                    break

                if not table_created:
                    # Derive columns from the select clause so we don't miss
                    # any columns that happen to be all-NULL in the first batch.
                    if select and select != "*":
                        columns = [c.strip() for c in select.split(",")]
                    else:
                        columns = list(rows[0].keys())
                    # Geo/numeric columns get REAL type — SQLite coerces on
                    # insert, and typed columns make the geo indexes usable
                    # (CAST-wrapped TEXT columns forced full scans).
                    col_defs = ", ".join(
                        f"[{c}] REAL" if c in real_columns else f"[{c}] TEXT"
                        for c in columns
                    )
                    self._conn.execute(
                        f"CREATE TABLE [{table}] ({col_defs})"
                    )
                    table_created = True

                # Bulk insert — serialize any dict/list values to JSON
                # Use the canonical column list from table creation
                placeholders = ", ".join("?" for _ in columns)
                col_names = ", ".join(f"[{c}]" for c in columns)

                def _val(v):
                    if isinstance(v, (dict, list)):
                        return json.dumps(v)
                    return v

                self._conn.executemany(
                    f"INSERT INTO [{table}] ({col_names}) VALUES ({placeholders})",
                    [tuple(_val(r.get(c)) for c in columns) for r in rows],
                )
                self._conn.commit()

                total += len(rows)
                offset += page_size

                if not quiet:
                    log.info(
                        "  ... %s rows downloaded so far", f"{total:,}"
                    )

                if len(rows) < page_size:
                    break  # last page

                time.sleep(0.3)  # courtesy delay

        finally:
            client.close()

        return total

    # Overpass (OpenStreetMap) bulk download

    _OVERPASS_URL = "https://overpass-api.de/api/interpreter"
    _OVERPASS_QUERY = """
[out:json][timeout:120];
(
  node["shop"="supermarket"](40.49,-74.26,40.92,-73.70);
  node["shop"="convenience"](40.49,-74.26,40.92,-73.70);
  node["amenity"="pharmacy"](40.49,-74.26,40.92,-73.70);
  node["leisure"="fitness_centre"](40.49,-74.26,40.92,-73.70);
  node["shop"="laundry"](40.49,-74.26,40.92,-73.70);
  node["amenity"="cafe"](40.49,-74.26,40.92,-73.70);
  node["amenity"="restaurant"](40.49,-74.26,40.92,-73.70);
);
out body;
"""

    def _overpass_download(self, table: str, *, quiet: bool) -> int:
        """Download all NYC amenity nodes from Overpass into *table*."""
        self._conn.execute(f"DROP TABLE IF EXISTS [{table}]")
        self._conn.execute(
            f"CREATE TABLE [{table}] "
            "(osm_id INTEGER, category TEXT, name TEXT, lat REAL, lon REAL)"
        )
        self._conn.commit()

        data = urllib.parse.urlencode({"data": self._OVERPASS_QUERY}).encode()
        req = urllib.request.Request(
            self._OVERPASS_URL, data=data,
            headers={"User-Agent": "AptHunt/1.0"},
        )
        if not quiet:
            log.info("amenities: requesting Overpass API (all NYC) ...")
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())

        rows: list[tuple] = []
        for el in body.get("elements", []):
            tags = el.get("tags", {})
            shop = tags.get("shop", "")
            amenity = tags.get("amenity", "")
            leisure = tags.get("leisure", "")

            if shop in ("supermarket", "convenience"):
                cat = "grocery"
            elif amenity == "pharmacy":
                cat = "pharmacy"
            elif leisure == "fitness_centre":
                cat = "gym"
            elif shop == "laundry":
                cat = "laundry"
            elif amenity in ("cafe", "restaurant"):
                cat = "dining"
            else:
                continue  # should not happen given the query, but be safe

            rows.append((
                el.get("id"),
                cat,
                tags.get("name", ""),
                el.get("lat"),
                el.get("lon"),
            ))

        self._conn.executemany(
            f"INSERT INTO [{table}] (osm_id, category, name, lat, lon) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        if not quiet:
            log.info("amenities: %s nodes downloaded", f"{len(rows):,}")
        return len(rows)

    # Overpass (OpenStreetMap) road-network download.
    #
    # Major roads only (motorway → tertiary). Each way's polyline is
    # sampled into a point every ~ROAD_SAMPLE_SPACING_M metres so scorers
    # can use the ordinary bbox/circle point queries — no line-geometry
    # math at scoring time.

    _ROAD_CLASSES = (
        "motorway", "motorway_link", "trunk", "trunk_link",
        "primary", "secondary", "tertiary",
    )
    ROAD_SAMPLE_SPACING_M = 50.0

    # NYC bbox split into 4 quadrants (requested sequentially) so each
    # Overpass response stays a manageable size. (south, west, north, east)
    _NYC_QUADRANTS = (
        (40.49, -74.26, 40.705, -73.98),   # SW
        (40.49, -73.98, 40.705, -73.70),   # SE
        (40.705, -74.26, 40.92, -73.98),   # NW
        (40.705, -73.98, 40.92, -73.70),   # NE
    )

    def _overpass_roads_download(self, table: str, *, quiet: bool) -> int:
        """Download NYC major-road ways from Overpass, sampled to points.

        Ways straddling a quadrant boundary come back from more than one
        request (with their FULL geometry each time — ``out geom;`` does
        not clip) so they are deduped by way_id and sampled exactly once.
        """
        self._conn.execute(f"DROP TABLE IF EXISTS [{table}]")
        self._conn.execute(
            f"CREATE TABLE [{table}] "
            "(way_id INTEGER, road_class TEXT, name TEXT, lanes INTEGER, "
            "lat REAL, lon REAL)"
        )
        self._conn.commit()

        classes = "|".join(self._ROAD_CLASSES)
        # Dedupe at POINT level, not way level: with a bbox filter Overpass
        # clips `out geom` to the quadrant (outside nodes lack coordinates),
        # so a way straddling quadrants returns DIFFERENT geometry each time.
        # Way-level dedupe kept only the first quadrant's run and punched
        # holes in every long highway (the BQE lost most of Williamsburg).
        seen_pts: set = set()
        total = 0

        def _fetch_tile(bbox) -> dict:
            """One Overpass request with retry across mirrors; raises on
            persistent failure so the caller can subdivide."""
            s, w, n, e = bbox
            query = (
                "[out:json][timeout:300];\n"
                f'way["highway"~"^({classes})$"]({s},{w},{n},{e});\n'
                "out geom;\n"
            )
            data = urllib.parse.urlencode({"data": query}).encode()
            mirrors = [self._OVERPASS_URL,
                       "https://overpass.kumi.systems/api/interpreter"]
            last_exc = None
            for attempt in range(2):
                req = urllib.request.Request(
                    mirrors[attempt], data=data,
                    headers={"User-Agent": "AptHunt/1.0"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=600) as resp:
                        return json.loads(resp.read())
                except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                    code = getattr(exc, "code", None)
                    if code in (429, 502, 503, 504) or code is None:
                        last_exc = exc
                        time.sleep(20)
                        continue
                    raise
            raise last_exc

        # Adaptive tiling: dense tiles (midtown Manhattan) 504 under load
        # no matter the retry policy — on transient failure a tile splits
        # into quarters and re-queues (max depth 3). Point-level dedupe
        # makes overlaps harmless.
        tiles = [(q, 0) for q in self._NYC_QUADRANTS]
        tile_no = 0
        while tiles:
            (s, w, n, e), depth = tiles.pop(0)
            tile_no += 1
            if tile_no > 1:
                time.sleep(8)  # be polite between requests
            if not quiet:
                log.info("roads: tile %d (depth %d) %.3f,%.3f→%.3f,%.3f ...",
                         tile_no, depth, s, w, n, e)
            try:
                body = _fetch_tile((s, w, n, e))
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                code = getattr(exc, "code", None)
                if (code in (429, 502, 503, 504) or code is None) and depth < 3:
                    ms, me = (s + n) / 2.0, (w + e) / 2.0
                    log.warning("roads: tile too heavy (%s) — splitting", code)
                    tiles.extend([
                        ((s, w, ms, me), depth + 1), ((s, me, ms, e), depth + 1),
                        ((ms, w, n, me), depth + 1), ((ms, me, n, e), depth + 1),
                    ])
                    continue
                raise

            rows: list[tuple] = []
            for el in body.get("elements", []):
                if el.get("type") != "way":
                    continue
                way_id = el.get("id")
                tags = el.get("tags", {})
                road_class = tags.get("highway", "")
                if road_class not in self._ROAD_CLASSES:
                    continue
                try:
                    # lanes may be "2;3" on splitting ways — keep the first
                    lanes = int(str(tags.get("lanes")).split(";")[0])
                except (ValueError, TypeError):
                    lanes = None
                coords = [
                    (g["lat"], g["lon"])
                    for g in (el.get("geometry") or [])
                    if g and g.get("lat") is not None and g.get("lon") is not None
                ]
                for plat, plon in _sample_polyline(
                    coords, self.ROAD_SAMPLE_SPACING_M
                ):
                    key = (way_id, round(plat, 5), round(plon, 5))
                    if key in seen_pts:
                        continue  # quadrant-boundary overlap
                    seen_pts.add(key)
                    rows.append(
                        (way_id, road_class, tags.get("name", ""), lanes,
                         plat, plon)
                    )

            self._conn.executemany(
                f"INSERT INTO [{table}] "
                "(way_id, road_class, name, lanes, lat, lon) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
            total += len(rows)
            if not quiet:
                log.info(
                    "roads: tile %d — %s sampled points (running total %s)",
                    tile_no, f"{len(rows):,}", f"{total:,}",
                )
        return total

    def _build_indexes(self, table: str, geo_columns: list[str]):
        """Create indexes on geo columns for fast bbox queries."""
        for col in geo_columns:
            idx_name = f"idx_{table}_{col}"
            self._conn.execute(
                f"CREATE INDEX IF NOT EXISTS [{idx_name}] ON [{table}] ([{col}])"
            )
        self._conn.commit()

    def _is_fresh(self, name: str, refresh_days: int) -> bool:
        """Check if a dataset was refreshed within its cadence."""
        meta = self._get_meta(name)
        if not meta:
            return False
        try:
            refreshed = datetime.fromisoformat(meta["refreshed_at"])
            return datetime.now(timezone.utc) - refreshed < timedelta(days=refresh_days)
        except (ValueError, TypeError):
            return False

    def _get_meta(self, name: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM _data_meta WHERE dataset = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def _table_exists(self, table: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    def _run_post_process(self, hook: str, table: str):
        """Run a named post-processing hook on a freshly-downloaded table."""
        if hook == "parks_centroid":
            self._pp_parks_centroid(table)
        elif hook == "null_zero_latlon":
            self._conn.execute(
                f"UPDATE [{table}] SET latitude=NULL, longitude=NULL "
                "WHERE latitude IS NOT NULL AND ABS(latitude) < 1"
            )
            self._conn.commit()
        elif hook == "bbl_from_boroid_block_lot":
            self._pp_build_bbl(table, "boroid", "block", "lot")
        elif hook == "bbl_from_boro_block_lot":
            self._pp_build_bbl(table, "boro", "block", "lot")
        elif hook == "bbl_from_borough_block_lot":
            self._pp_build_bbl(table, "borough", "block", "lot")
        elif hook == "extract_georeference":
            self._pp_extract_point(table, "georeference")
        elif hook == "extract_the_geom_point":
            self._pp_extract_point(table, "the_geom")
        elif hook == "dedupe_bus_stops":
            self._pp_dedupe_bus_stops(table)
        elif hook == "rebuild_crime_merged":
            self._rebuild_crime_merged()
        elif hook == "sample_truck_routes":
            self._pp_sample_truck_routes(table)
        elif hook == "keep_latest_year":
            self._conn.execute(
                f"DELETE FROM [{table}] WHERE year != "
                f"(SELECT MAX(year) FROM [{table}])"
            )
            self._conn.commit()
        else:
            log.warning("Unknown post-process hook: %s", hook)

    def _rebuild_crime_merged(self):
        """Materialize ds_crime from whichever crime source tables exist.

        Historic rows win on cmplnt_num collisions (they're the vetted
        record); YTD fills the recent months the historic feed hasn't
        published yet. Runs after either source refreshes.
        """
        sources = [
            s for s in DERIVED_DATASETS["crime"]["sources"]
            if self._table_exists(f"ds_{s}")
        ]
        if not sources:
            return
        staging = "_staging_crime_merged"
        self._conn.commit()
        prev_isolation = self._conn.isolation_level
        try:
            self._conn.isolation_level = None
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(f"DROP TABLE IF EXISTS [{staging}]")
            self._conn.execute(
                f"CREATE TABLE [{staging}] ("
                "cmplnt_num TEXT, cmplnt_fr_dt TEXT, law_cat_cd TEXT, "
                "latitude REAL, longitude REAL)"
            )
            self._conn.execute(
                f"CREATE UNIQUE INDEX [idx_{staging}_num] ON [{staging}] (cmplnt_num)"
            )
            for src in sources:  # historic listed first → wins dedupe
                self._conn.execute(
                    f"INSERT OR IGNORE INTO [{staging}] "
                    "(cmplnt_num, cmplnt_fr_dt, law_cat_cd, latitude, longitude) "
                    f"SELECT cmplnt_num, cmplnt_fr_dt, law_cat_cd, latitude, longitude "
                    f"FROM [ds_{src}] WHERE cmplnt_num IS NOT NULL"
                )
            self._conn.execute("DROP TABLE IF EXISTS ds_crime")
            self._conn.execute(f"ALTER TABLE [{staging}] RENAME TO ds_crime")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ds_crime_latitude ON ds_crime (latitude)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ds_crime_longitude ON ds_crime (longitude)"
            )
            self._conn.execute("COMMIT")
        except Exception:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        finally:
            self._conn.isolation_level = prev_isolation
        n = self._conn.execute("SELECT COUNT(*) FROM ds_crime").fetchone()[0]
        log.info("ds_crime merged: %s rows from %s", f"{n:,}", " + ".join(sources))

    def _pp_build_bbl(self, table: str, boro_col: str, block_col: str, lot_col: str):
        """Derive a normalized 10-digit BBL column from boro/block/lot parts."""
        try:
            self._conn.execute(f"ALTER TABLE [{table}] ADD COLUMN bbl TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists (some datasets ship their own)
        self._conn.execute(
            f"UPDATE [{table}] SET bbl = "
            f"CAST(CAST([{boro_col}] AS INTEGER) AS TEXT) || "
            f"printf('%05d', CAST([{block_col}] AS INTEGER)) || "
            f"printf('%04d', CAST([{lot_col}] AS INTEGER)) "
            f"WHERE [{boro_col}] IS NOT NULL AND [{block_col}] IS NOT NULL "
            f"AND [{lot_col}] IS NOT NULL AND (bbl IS NULL OR bbl = '')"
        )
        self._conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_bbl ON [{table}] (bbl)"
        )
        self._conn.commit()

    def _pp_extract_point(self, table: str, geom_col: str):
        """Extract latitude/longitude REAL columns from a GeoJSON Point column."""
        for col in ("latitude", "longitude"):
            try:
                self._conn.execute(f"ALTER TABLE [{table}] ADD COLUMN {col} REAL")
            except sqlite3.OperationalError:
                pass
        rows = self._conn.execute(
            f"SELECT rowid, [{geom_col}] FROM [{table}] "
            f"WHERE [{geom_col}] IS NOT NULL"
        ).fetchall()
        updates = []
        for rowid, raw in rows:
            try:
                geom = json.loads(raw) if isinstance(raw, str) else raw
                coords = geom.get("coordinates") or []
                if len(coords) >= 2:
                    updates.append((float(coords[1]), float(coords[0]), rowid))
            except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
                continue
        self._conn.executemany(
            f"UPDATE [{table}] SET latitude=?, longitude=? WHERE rowid=?", updates
        )
        self._build_indexes(table, ["latitude", "longitude"])
        self._conn.commit()
        log.info("%s: extracted %d points from %s", table, len(updates), geom_col)

    def _pp_sample_truck_routes(self, table: str):
        """Explode truck-route line geometries into ~50 m sampled points.

        Each LineString/MultiLineString in ``the_geom`` is sampled every
        ~ROAD_SAMPLE_SPACING_M metres.  Point rows are appended to the
        same table with (route_type, lat, lon) populated (the original
        SODA columns stay NULL on point rows — harmless extra columns),
        then the original line rows (lat IS NULL) are deleted so the
        table contains pure point rows for bbox/circle queries.
        """
        for col, typ in (
            ("route_type", "TEXT"), ("lat", "REAL"), ("lon", "REAL"),
        ):
            try:
                self._conn.execute(
                    f"ALTER TABLE [{table}] ADD COLUMN {col} {typ}"
                )
            except sqlite3.OperationalError:
                pass  # column already exists

        rows = self._conn.execute(
            f"SELECT rowid, routetype, the_geom FROM [{table}] "
            f"WHERE the_geom IS NOT NULL"
        ).fetchall()

        inserts: list[tuple] = []
        for _rowid, routetype, raw in rows:
            try:
                geom = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(geom, dict):
                continue
            gtype = geom.get("type", "")
            coords = geom.get("coordinates") or []
            if gtype == "LineString":
                lines = [coords]
            elif gtype == "MultiLineString":
                lines = coords
            else:
                continue
            for line in lines:
                latlon = [
                    (c[1], c[0]) for c in line
                    if isinstance(c, (list, tuple)) and len(c) >= 2
                ]
                for plat, plon in _sample_polyline(
                    latlon, self.ROAD_SAMPLE_SPACING_M
                ):
                    inserts.append((routetype or "", plat, plon))

        self._conn.executemany(
            f"INSERT INTO [{table}] (route_type, lat, lon) VALUES (?, ?, ?)",
            inserts,
        )
        # Drop the original line rows — consumers only see point rows.
        self._conn.execute(f"DELETE FROM [{table}] WHERE lat IS NULL")
        self._conn.commit()
        log.info("%s: sampled %d truck-route points", table, len(inserts))

    def _pp_dedupe_bus_stops(self, table: str):
        """Collapse stop x route x direction rows to one row per stop with
        aggregated route list and route count."""
        self._conn.execute(f"DROP TABLE IF EXISTS [{table}__dedup]")
        self._conn.execute(
            f"CREATE TABLE [{table}__dedup] AS "
            f"SELECT stop_id, MIN(stop_name) AS stop_name, "
            f"       MIN(latitude) AS latitude, MIN(longitude) AS longitude, "
            f"       GROUP_CONCAT(DISTINCT route_short_name) AS routes, "
            f"       COUNT(DISTINCT route_short_name) AS route_count "
            f"FROM [{table}] WHERE latitude IS NOT NULL GROUP BY stop_id"
        )
        self._conn.execute(f"DROP TABLE [{table}]")
        self._conn.execute(f"ALTER TABLE [{table}__dedup] RENAME TO [{table}]")
        self._conn.commit()

    def _pp_parks_centroid(self, table: str):
        """
        Compute centroid lat/lon from multipolygon GeoJSON for each park.

        Adds ``centroid_lat`` and ``centroid_lon`` columns and populates
        them with the average of all polygon vertices.  These are used
        for fast bbox pre-filtering in local queries.
        """
        # Add centroid columns
        try:
            self._conn.execute(f"ALTER TABLE [{table}] ADD COLUMN centroid_lat REAL")
            self._conn.execute(f"ALTER TABLE [{table}] ADD COLUMN centroid_lon REAL")
        except sqlite3.OperationalError:
            pass  # columns already exist

        rows = self._conn.execute(
            f"SELECT rowid, multipolygon FROM [{table}]"
        ).fetchall()

        for row in rows:
            rowid = row[0]
            mp_raw = row[1]
            if not mp_raw:
                continue

            try:
                geom = json.loads(mp_raw) if isinstance(mp_raw, str) else mp_raw
            except (json.JSONDecodeError, TypeError):
                continue

            coords = _extract_all_coords(geom)
            if not coords:
                continue

            avg_lon = sum(c[0] for c in coords) / len(coords)
            avg_lat = sum(c[1] for c in coords) / len(coords)

            self._conn.execute(
                f"UPDATE [{table}] SET centroid_lat=?, centroid_lon=? WHERE rowid=?",
                (avg_lat, avg_lon, rowid),
            )

        # Index on centroids
        self._conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_clat ON [{table}] (centroid_lat)"
        )
        self._conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_clon ON [{table}] (centroid_lon)"
        )
        self._conn.commit()
        log.info("Parks: computed centroids for %d rows", len(rows))


def _sample_polyline(
    coords: list[tuple[float, float]], spacing_m: float,
) -> list[tuple[float, float]]:
    """Sample a polyline of (lat, lon) vertices into points every ~spacing_m.

    Walks the polyline accumulating travelled distance (equirectangular
    approximation — sub-centimetre error at NYC latitudes over ≤50 m
    steps), emitting the first vertex and then one linearly-interpolated
    point every ``spacing_m`` metres of arc length.  A way shorter than
    ``spacing_m`` still yields its first vertex, so no road disappears.
    """
    if not coords:
        return []
    pts: list[tuple[float, float]] = [coords[0]]
    carried = 0.0   # metres travelled since the last emitted point
    for (lat1, lon1), (lat2, lon2) in zip(coords, coords[1:]):
        m_per_deg_lon = 111_320.0 * math.cos(
            math.radians((lat1 + lat2) / 2.0)
        )
        dy = (lat2 - lat1) * 111_320.0
        dx = (lon2 - lon1) * m_per_deg_lon
        seg = math.hypot(dx, dy)
        if seg <= 0:
            continue
        d = spacing_m - carried  # distance into this segment of next emission
        while d <= seg:
            f = d / seg
            pts.append(
                (lat1 + f * (lat2 - lat1), lon1 + f * (lon2 - lon1))
            )
            d += spacing_m
        carried = (carried + seg) % spacing_m
    return pts


def _extract_all_coords(geom: dict) -> list[tuple[float, float]]:
    """Extract all [lon, lat] vertices from a GeoJSON geometry."""
    gtype = geom.get("type", "")
    raw = geom.get("coordinates", [])
    points: list[tuple[float, float]] = []

    if gtype == "MultiPolygon":
        for polygon in raw:
            for ring in polygon:
                points.extend((c[0], c[1]) for c in ring)
    elif gtype == "Polygon":
        for ring in raw:
            points.extend((c[0], c[1]) for c in ring)
    elif gtype == "Point":
        if len(raw) >= 2:
            points.append((raw[0], raw[1]))

    return points
