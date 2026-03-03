"""
Neighborhood data provider.

Provides:
  - About-the-neighborhood descriptions
  - Median rent prices per neighborhood
  - Nearby neighborhoods (adjacent)
  - Nearby POIs (parks, schools, grocery, etc.) from scoring data
  - Transit stations with route letters
  - Pet policy extraction from amenities
  - Amenity categorization
  - Open house placeholder
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional

from api.models import (
    CategorizedAmenities,
    NeighborhoodInfo,
    POI,
    TransitStation,
)


# ── Neighborhood descriptions ──────────────────────────────────
# Curated descriptions for NYC neighborhoods. In production these
# could come from a CMS or an external dataset; here we embed a selection.

_NEIGHBORHOOD_DESCRIPTIONS: dict[str, str] = {
    "Financial District": (
        "The Financial District is busiest on weekdays, when office workers and visitors fill its "
        "historic streets, museums, and waterfront promenades. Evenings and weekends are far quieter, with "
        "nightlife limited to a handful of blocks and most residents living in full-service "
        "high-rise towers. Excellent subway access with multiple lines converging "
        "at Fulton Center makes it easy to reach any corner of the city."
    ),
    "Tribeca": (
        "Tribeca is a coveted residential neighborhood known for its cobblestone streets, converted loft "
        "buildings, and celebrity residents. The area has excellent dining and a strong community feel despite "
        "its upscale reputation. Hudson River Park runs along its western edge, and the neighborhood is home "
        "to the Tribeca Film Festival each spring."
    ),
    "Soho": (
        "SoHo (South of Houston) is a trendy neighborhood famous for its cast-iron architecture, art "
        "galleries, and high-end shopping along Broadway and Prince Street. While it's become more "
        "commercial, the side streets maintain a charming residential character with loft-style living."
    ),
    "Greenwich Village": (
        "The Village is one of Manhattan's most storied neighborhoods, with tree-lined streets, brownstones, "
        "and a bohemian legacy. Home to NYU, Washington Square Park, and a thriving dining scene, it offers "
        "a rare blend of city energy and neighborhood charm. Excellent transit options and walkability."
    ),
    "West Village": (
        "The West Village features winding streets, historic townhouses, and a laid-back atmosphere that "
        "feels worlds away from Midtown. It's one of the city's most desirable neighborhoods, with charming "
        "cafés, boutiques, and the Hudson River waterfront just steps away."
    ),
    "East Village": (
        "The East Village is a vibrant, eclectic neighborhood with a punk-rock past and a diverse present. "
        "Tompkins Square Park anchors the community, surrounded by an incredible array of restaurants, bars, "
        "and independent shops. It's popular with younger renters looking for character and nightlife."
    ),
    "Lower East Side": (
        "Once a working-class immigrant neighborhood, the Lower East Side has evolved into a dynamic mix of "
        "old and new. Historic tenements sit alongside modern luxury buildings. The area is known for its "
        "nightlife, art galleries, and diverse food scene spanning from dim sum to craft cocktails."
    ),
    "Chelsea": (
        "Chelsea is a cultural hub anchored by the High Line, the gallery district, and Chelsea Market. "
        "The neighborhood attracts art lovers, foodies, and professionals who appreciate its central location "
        "and excellent transit access. Hudson Yards and the waterfront add modern amenities."
    ),
    "Midtown": (
        "Midtown is the city's commercial heart, home to Times Square, Rockefeller Center, and Grand Central. "
        "While it's bustling during the day, residential pockets on the far east and west sides offer "
        "surprisingly livable streets with easy access to everything the city offers."
    ),
    "Upper East Side": (
        "The Upper East Side is one of Manhattan's most established residential neighborhoods, known for "
        "Museum Mile, Central Park access, and elegant pre-war architecture. Lexington and Third Avenues "
        "offer everyday shopping and dining, while Park Avenue maintains its grand character."
    ),
    "Upper West Side": (
        "The Upper West Side is a family-friendly neighborhood bordered by Central Park and Riverside Park. "
        "Home to Lincoln Center, the American Museum of Natural History, and Columbia University, it offers "
        "a cultivated atmosphere with excellent schools and abundant green space."
    ),
    "Harlem": (
        "Harlem is a historically significant neighborhood experiencing a renaissance, with new restaurants "
        "and shops joining beloved institutions. The area offers beautiful brownstones, Marcus Garvey Park, "
        "and a strong sense of community. Rents remain more accessible than downtown Manhattan."
    ),
    "Williamsburg": (
        "Williamsburg is Brooklyn's trendiest neighborhood, known for its vibrant arts scene, craft breweries, "
        "and waterfront parks. The L train provides quick access to Manhattan, and the neighborhood offers "
        "everything from vintage shops to Michelin-starred restaurants along Bedford Avenue."
    ),
    "Bushwick": (
        "Bushwick has emerged as a creative hub with street art, DIY galleries, and a diverse food scene. "
        "The neighborhood offers more affordable rents than neighboring Williamsburg while maintaining "
        "excellent energy and a strong arts community. Multiple subway lines provide good transit access."
    ),
    "Park Slope": (
        "Park Slope is one of Brooklyn's most sought-after neighborhoods, famous for its beautiful brownstones, "
        "tree-lined streets, and proximity to Prospect Park. It's a favorite among families, with excellent "
        "schools, charming restaurants along 5th and 7th Avenues, and a strong community feel."
    ),
    "Brooklyn Heights": (
        "Brooklyn Heights is the borough's most historic neighborhood, with stunning brownstones and the "
        "iconic Brooklyn Heights Promenade overlooking Manhattan. Quiet, tree-lined streets and proximity "
        "to Brooklyn Bridge Park make it one of the most desirable residential areas in the city."
    ),
    "DUMBO": (
        "DUMBO (Down Under the Manhattan Bridge Overpass) features cobblestone streets, converted warehouse "
        "lofts, and spectacular Manhattan views. Brooklyn Bridge Park runs along the waterfront, and the "
        "neighborhood has become a tech hub alongside its arts and dining scene."
    ),
    "Crown Heights": (
        "Crown Heights is a diverse, evolving neighborhood with beautiful pre-war architecture, the Brooklyn "
        "Museum, and the Brooklyn Botanic Garden. Franklin Avenue has become a dining destination, and the "
        "neighborhood offers good value relative to its amenities and transit access."
    ),
    "Bed-Stuy": (
        "Bedford-Stuyvesant boasts some of Brooklyn's most impressive brownstone architecture. The neighborhood "
        "has a strong residential character with tree-lined blocks, local restaurants and bars along "
        "Nostrand and Tompkins Avenues, and improving transit connections."
    ),
    "Prospect Heights": (
        "Prospect Heights sits adjacent to Prospect Park and the Barclays Center, offering easy access to "
        "both green space and entertainment. Vanderbilt Avenue is lined with excellent restaurants and shops, "
        "and the neighborhood has a diverse, community-oriented feel."
    ),
    "Astoria": (
        "Astoria is one of Queens' most popular neighborhoods, known for its incredible ethnic dining scene "
        "(especially Greek and Middle Eastern), Astoria Park, and proximity to Manhattan via the N/W trains. "
        "Steinway Street and Broadway offer abundant shopping and nightlife."
    ),
    "Long Island City": (
        "Long Island City has transformed from an industrial area into a dynamic residential neighborhood "
        "with gleaming towers, waterfront parks, and cultural institutions like MoMA PS1. One-stop from "
        "Midtown on the 7 train, it offers some of the best skyline views in the city."
    ),
    "Hunters Point": (
        "Hunters Point is the waterfront portion of Long Island City, featuring modern high-rise developments "
        "and stunning Manhattan views across the East River. The 7 train provides quick access to Grand Central "
        "and Times Square, and Gantry Plaza State Park is a neighborhood gem."
    ),
    "Sunnyside": (
        "Sunnyside is a diverse, affordable neighborhood with a small-town feel. Sunnyside Gardens, a planned "
        "community built in the 1920s, gives the area a unique character. The 7 train provides a direct line "
        "to Manhattan, and Queens Boulevard offers shopping and services."
    ),
    "Jackson Heights": (
        "Jackson Heights is one of the most ethnically diverse neighborhoods in the world, with an incredible "
        "array of cuisines from South Asian to Latin American along Roosevelt Avenue and 37th Avenue. "
        "Historic garden apartments and excellent E/F/M/R/7 train access add to its appeal."
    ),
    "Fordham": (
        "Fordham is a bustling Bronx neighborhood centered around Fordham Road, one of the borough's main "
        "commercial strips. Home to Fordham University and close to the Bronx Zoo and New York Botanical "
        "Garden, it offers a mix of urban energy and green spaces."
    ),
    "Riverdale": (
        "Riverdale is one of the Bronx's most affluent neighborhoods, with a suburban feel, leafy streets, "
        "and beautiful homes along the Hudson River. Wave Hill, a public garden, offers stunning views, "
        "and the area has excellent schools and a tight-knit community."
    ),
    "Kingsbridge": (
        "Kingsbridge is a residential Bronx neighborhood with a strong community feel, diverse dining options, "
        "and Van Cortlandt Park — one of the city's largest parks — right at its doorstep. The 1 train "
        "provides a direct line to Manhattan."
    ),
    "Bay Ridge": (
        "Bay Ridge is a quiet, family-friendly Brooklyn neighborhood with beautiful views of the Verrazzano "
        "Bridge and the waterfront. Third and Fifth Avenues offer abundant dining and shopping, and the "
        "area maintains a strong sense of community with relatively affordable rents."
    ),
    "Sunset Park": (
        "Sunset Park is a diverse neighborhood with a thriving Chinatown along 8th Avenue, views of the "
        "Statue of Liberty from the hilltop park, and an increasingly vibrant waterfront area at Industry City. "
        "The D/N/R trains provide good transit access."
    ),
    "Flatbush": (
        "Flatbush is a large, diverse Brooklyn neighborhood with Caribbean influence, beautiful Victorian "
        "homes along Ditmas Park, and easy access to Prospect Park. The area offers good transit options "
        "and a wide range of dining, from jerk chicken to artisanal bakeries."
    ),
    "Greenpoint": (
        "Greenpoint is a charming neighborhood at Brooklyn's northern tip, with a strong Polish heritage, "
        "picturesque streets, and a growing food scene. The waterfront offers Manhattan views, and "
        "McCarren Park provides green space shared with neighboring Williamsburg."
    ),
    "Battery Park City": (
        "Battery Park City is a planned community on Manhattan's southwestern tip, featuring waterfront "
        "esplanades, manicured parks, and modern residential towers. It's quiet and family-friendly, with "
        "easy access to the Financial District and stunning Hudson River sunset views."
    ),
    "Chinatown": (
        "Manhattan's Chinatown is a bustling, vibrant neighborhood with some of the city's best and most "
        "affordable dining. The area retains a strong cultural identity with markets, temples, and traditions. "
        "Multiple subway lines converge nearby, making it extremely well-connected."
    ),
    "Hell's Kitchen": (
        "Hell's Kitchen (Clinton) has transformed from its gritty past into a vibrant neighborhood with "
        "Restaurant Row, access to Hudson River Park, and proximity to Times Square and the theater district. "
        "Ninth and Tenth Avenues are lined with diverse dining options."
    ),
    "Morningside Heights": (
        "Morningside Heights is home to Columbia University and the Cathedral of St. John the Divine, "
        "giving it an academic village atmosphere. Morningside Park provides beautiful green space, and "
        "the neighborhood offers a mix of student life and residential calm."
    ),
    "Washington Heights": (
        "Washington Heights is a vibrant, predominantly Dominican neighborhood with affordable rents, "
        "Fort Tryon Park and the Cloisters museum, and excellent views from its hilltop position. "
        "The A train provides express service to Midtown, and the George Washington Bridge is nearby."
    ),
    "Inwood": (
        "Inwood sits at Manhattan's northern tip, offering Inwood Hill Park — the last natural forest in "
        "Manhattan — and affordable rents. The neighborhood has a strong Dominican community, local shops "
        "along Dyckman Street, and the A train for express access downtown."
    ),
    "Cobble Hill": (
        "Cobble Hill is a charming Brooklyn neighborhood with brownstone-lined streets, boutique shops on "
        "Court and Smith Streets, and a family-friendly atmosphere. Close to Brooklyn Bridge Park and "
        "with excellent transit options, it's one of the borough's most desirable areas."
    ),
    "Carroll Gardens": (
        "Carroll Gardens is known for its wide tree-lined streets, Italian-American heritage, and charming "
        "brownstones with deep front gardens. Smith Street offers excellent dining, and the neighborhood "
        "maintains a village-like feel with strong community ties."
    ),
    "Fort Greene": (
        "Fort Greene is a culturally rich Brooklyn neighborhood anchored by BAM (Brooklyn Academy of Music) "
        "and Fort Greene Park. Beautiful brownstones, diverse restaurants along DeKalb Avenue, and excellent "
        "transit access at Atlantic Terminal make it highly desirable."
    ),
    "Clinton Hill": (
        "Clinton Hill neighbors Fort Greene and shares its brownstone beauty and cultural richness. Pratt "
        "Institute brings an artistic energy, and the neighborhood offers a quieter, more residential "
        "alternative with excellent dining along Myrtle and DeKalb Avenues."
    ),
    "Prospect Lefferts Gardens": (
        "Prospect Lefferts Gardens (PLG) borders the southeastern edge of Prospect Park, offering direct "
        "park access and beautiful pre-war architecture. The diverse neighborhood has an emerging food "
        "scene on Flatbush and Rogers Avenues, with the B/Q trains for transit."
    ),
    "Windsor Terrace": (
        "Windsor Terrace is a quiet, family-friendly neighborhood tucked between Prospect Park and Green-Wood "
        "Cemetery. It has a small-town feel with local pubs, bakeries, and an involved community, plus "
        "easy access to the F/G trains."
    ),
    "Red Hook": (
        "Red Hook is a waterfront neighborhood with an industrial-chic character, home to IKEA, Fairway, "
        "and the Red Hook Ball Fields food vendors. While transit is limited, the area offers unique "
        "waterfront living, artist studios, and spectacular Statue of Liberty views."
    ),
    "Gowanus": (
        "Gowanus is rapidly evolving from its industrial roots into a mixed-use neighborhood. The Gowanus "
        "Canal cleanup and rezoning are bringing new residential development, while the area already boasts "
        "breweries, music venues, and Whole Foods. The F/G/R trains are nearby."
    ),
    "South Slope": (
        "South Slope extends the Park Slope vibe southward, offering more affordable brownstone living with "
        "a growing restaurant scene along 5th Avenue. The neighborhood is quieter than its northern neighbor "
        "but still benefits from Prospect Park proximity and good transit."
    ),
    "Midtown East": (
        "Midtown East encompasses Grand Central Terminal, the United Nations, and some of Manhattan's most "
        "prestigious office buildings. Residential options tend toward luxury, with easy access to Metro-North "
        "and multiple subway lines."
    ),
    "Murray Hill": (
        "Murray Hill is a residential enclave in Midtown East, popular with young professionals. The "
        "neighborhood offers a mix of pre-war walk-ups and modern high-rises, with Lexington Avenue dining "
        "and easy access to Grand Central Terminal."
    ),
    "Gramercy": (
        "Gramercy is defined by its eponymous private park, surrounded by elegant townhouses and pre-war "
        "buildings. The neighborhood has a refined, quiet character with excellent restaurants along Park "
        "Avenue South and Irving Place."
    ),
    "Flatiron": (
        "The Flatiron District is a dynamic area centered around Madison Square Park and the iconic Flatiron "
        "Building. It's a hub for tech companies, with excellent dining along Broadway and Park Avenue South, "
        "and strong transit connections."
    ),
    "NoHo": (
        "NoHo (North of Houston) is a small, exclusive neighborhood with cobblestone streets, cast-iron "
        "buildings, and a mix of high-end retail and residential lofts. It's well-connected by multiple "
        "subway lines at Broadway-Lafayette and Bleecker Street stations."
    ),
    "Nolita": (
        "Nolita (North of Little Italy) is a charming neighborhood known for its boutique shopping, "
        "Instagram-worthy cafés, and European village atmosphere. Just a few blocks wide, it packs in "
        "tremendous character and some of the city's best casual dining."
    ),
    "Two Bridges": (
        "Two Bridges sits between the Brooklyn and Manhattan Bridges along the East River waterfront. "
        "The neighborhood features a mix of historic housing projects and new luxury towers, with "
        "proximity to Chinatown's dining and the East River Greenway."
    ),
    "Kips Bay": (
        "Kips Bay is a quiet residential neighborhood on Manhattan's east side, with a mix of post-war "
        "high-rises and smaller buildings. It's convenient to NYU Langone Medical Center, has good "
        "restaurant options on Second and Third Avenues, and offers easy Midtown access."
    ),
    "Stuyvesant Town": (
        "Stuyvesant Town–Peter Cooper Village is a massive residential complex offering a campus-like "
        "setting with green lawns, playgrounds, and car-free pathways. It's one of Manhattan's most "
        "affordable options for families, with the L train and bus routes nearby."
    ),
    "Yorkville": (
        "Yorkville is the Upper East Side's northern section, more relaxed and affordable than the blocks "
        "closer to the park. Carl Schurz Park and the East River Esplanade offer waterfront green space, "
        "and the Second Avenue subway has improved transit access."
    ),
    "Mott Haven": (
        "Mott Haven is a South Bronx neighborhood experiencing rapid development, with new mixed-use "
        "projects along the Harlem River waterfront. It offers some of the most affordable rents near "
        "Manhattan, with the 6 train just one stop from 125th Street."
    ),
}


# ── Pet policy extraction ──────────────────────────────────────

_PET_AMENITY_KEYWORDS = {
    "pet friendly": "Pets allowed",
    "pets allowed": "Pets allowed",
    "cats allowed": "Cats allowed",
    "dogs allowed": "Dogs allowed",
    "cats and dogs": "Cats and dogs allowed",
    "no pets": "No pets allowed",
    "pet": "Pets allowed",
}


def extract_pet_policy(amenities: list[str]) -> str | None:
    """Extract pet policy from amenity list."""
    has_pet = False
    has_dog = False
    has_cat = False
    no_pet = False

    for a in amenities:
        lower = a.lower().strip()
        if "no pet" in lower:
            no_pet = True
        elif "dog" in lower:
            has_dog = True
        elif "cat" in lower:
            has_cat = True
        elif "pet" in lower:
            has_pet = True

    if no_pet:
        return "No pets allowed"
    if has_dog and has_cat:
        return "Cats and dogs allowed"
    if has_dog:
        return "Dogs allowed"
    if has_cat:
        return "Cats allowed"
    if has_pet:
        return "Pets allowed"
    return None


# ── Amenity categorization ─────────────────────────────────────

_AMENITY_CATEGORIES: dict[str, str] = {
    # Services
    "doorman": "services",
    "full-time doorman": "services",
    "part-time doorman": "services",
    "virtual doorman": "services",
    "concierge": "services",
    "elevator": "services",
    "laundry in building": "services",
    "live-in super": "services",
    "superintendent": "services",
    "package room": "services",
    "mail room": "services",
    "valet": "services",
    # Wellness
    "gym": "wellness",
    "fitness center": "wellness",
    "pool": "wellness",
    "swimming pool": "wellness",
    "sauna": "wellness",
    "spa": "wellness",
    "yoga studio": "wellness",
    "media room": "wellness",
    "game room": "wellness",
    "playroom": "wellness",
    "children's playroom": "wellness",
    "lounge": "wellness",
    "residents lounge": "wellness",
    "co-working space": "wellness",
    # Outdoor
    "roof deck": "outdoor",
    "rooftop deck": "outdoor",
    "roof terrace": "outdoor",
    "rooftop terrace": "outdoor",
    "garden": "outdoor",
    "courtyard": "outdoor",
    "patio": "outdoor",
    "terrace": "outdoor",
    "balcony": "outdoor",
    "outdoor space": "outdoor",
    # Convenience
    "parking": "convenience",
    "garage": "convenience",
    "parking garage": "convenience",
    "bike room": "convenience",
    "bicycle storage": "convenience",
    "bike storage": "convenience",
    "storage": "convenience",
    "storage room": "convenience",
    "cold storage": "convenience",
    # Unit features
    "dishwasher": "unit_features",
    "washer/dryer": "unit_features",
    "washer/dryer in unit": "unit_features",
    "in-unit washer/dryer": "unit_features",
    "laundry in unit": "unit_features",
    "central air": "unit_features",
    "central a/c": "unit_features",
    "air conditioning": "unit_features",
    "hardwood floors": "unit_features",
    "hardwood": "unit_features",
    "stainless steel appliances": "unit_features",
    "granite countertops": "unit_features",
    "marble bath": "unit_features",
    "high ceilings": "unit_features",
    "city view": "unit_features",
    "water view": "unit_features",
    "exposed brick": "unit_features",
    "fireplace": "unit_features",
    "walk-in closet": "unit_features",
    "private outdoor space": "unit_features",
    "furnished": "unit_features",
}


def categorize_amenities(amenities: list[str]) -> CategorizedAmenities:
    """Sort amenities into listing-page-style categories."""
    cats: dict[str, list[str]] = {
        "services": [],
        "wellness": [],
        "outdoor": [],
        "convenience": [],
        "unit_features": [],
    }

    for a in amenities:
        lower = a.lower().strip()
        # Try exact match first
        cat = _AMENITY_CATEGORIES.get(lower)
        if not cat:
            # Try substring match
            for keyword, c in _AMENITY_CATEGORIES.items():
                if keyword in lower:
                    cat = c
                    break
        if cat:
            if a not in cats[cat]:
                cats[cat].append(a)

    return CategorizedAmenities(**cats)


# ── Neighborhood info lookup ───────────────────────────────────

def get_neighborhood_info(
    conn: sqlite3.Connection,
    neighborhood: str,
) -> NeighborhoodInfo:
    """Get neighborhood description and median rental prices."""
    desc = _NEIGHBORHOOD_DESCRIPTIONS.get(neighborhood)

    # Compute median rent for 1BR and 2BR from our own data
    med_1br = _median_rent(conn, neighborhood, 1)
    med_2br = _median_rent(conn, neighborhood, 2)

    return NeighborhoodInfo(
        description=desc,
        median_rent_1br=med_1br,
        median_rent_2br=med_2br,
    )


def _median_rent(
    conn: sqlite3.Connection,
    neighborhood: str,
    beds: int,
) -> int | None:
    """Compute median rent for a bed count in a neighborhood."""
    rows = conn.execute(
        """
        SELECT price FROM listings
        WHERE UPPER(status) = 'ACTIVE'
          AND LOWER(neighborhood) = LOWER(?)
          AND beds = ?
          AND price > 0
        ORDER BY price
        """,
        (neighborhood, beds),
    ).fetchall()
    if not rows:
        return None
    n = len(rows)
    if n % 2 == 0:
        return (rows[n // 2 - 1][0] + rows[n // 2][0]) // 2
    return rows[n // 2][0]


# ── Nearby neighborhoods ──────────────────────────────────────

def get_nearby_neighborhoods(
    conn: sqlite3.Connection,
    neighborhood: str,
    borough: str,
    lat: float,
    lon: float,
    max_distance_deg: float = 0.02,  # ~2 km
    limit: int = 5,
) -> list[str]:
    """Find neighborhoods with active listings nearby."""
    rows = conn.execute(
        """
        SELECT DISTINCT neighborhood
        FROM listings
        WHERE UPPER(status) = 'ACTIVE'
          AND neighborhood IS NOT NULL
          AND LOWER(neighborhood) != LOWER(?)
          AND lat IS NOT NULL AND lon IS NOT NULL
          AND ABS(lat - ?) < ? AND ABS(lon - ?) < ?
        ORDER BY ABS(lat - ?) + ABS(lon - ?)
        LIMIT ?
        """,
        (neighborhood, lat, max_distance_deg, lon, max_distance_deg, lat, lon, limit * 3),
    ).fetchall()

    seen: list[str] = []
    for r in rows:
        name = r[0]
        if name not in seen:
            seen.append(name)
        if len(seen) >= limit:
            break
    return seen


# ── Nearby POIs ────────────────────────────────────────────────

def get_nearby_pois(row: dict) -> list[POI]:
    """
    Extract POIs from the scoring component columns already in the DB row.

    This pulls data that our scorers already computed:
    - Best park (name + distance)
    - Best school (name)
    - Convenience counts (grocery, pharmacy, gym, laundry, dining)
    """
    pois: list[POI] = []

    # Park
    park_name = row.get("parks_name")
    park_dist = row.get("parks_distance_m")
    if park_name and park_dist is not None:
        acres = row.get("parks_acres")
        label = f"{park_name}"
        if acres:
            label += f" ({acres:.0f} acres)" if acres >= 1 else f" ({acres:.1f} acres)"
        pois.append(POI(name=label, category="park", distance_m=int(park_dist)))

    # School
    school_name = row.get("school_name")
    if school_name:
        rating = row.get("school_rating")
        label = school_name
        if rating:
            label += f" (rating: {rating:.1f})"
        pois.append(POI(name=label, category="school", distance_m=0))

    # Community gardens from greenery
    garden_count = row.get("greenery_garden_count")
    if garden_count and garden_count > 0:
        pois.append(POI(
            name=f"{garden_count} community garden{'s' if garden_count > 1 else ''} within 500m",
            category="garden",
            distance_m=500,
        ))

    # Convenience POI summary
    for cat, col, label in [
        ("grocery", "convenience_grocery", "grocery/convenience stores"),
        ("pharmacy", "convenience_pharmacy", "pharmacies"),
        ("gym", "convenience_gym", "gyms/fitness centers"),
        ("laundry", "convenience_laundry", "laundromats"),
        ("restaurant", "convenience_dining", "restaurants & cafes"),
    ]:
        count = row.get(col)
        if count and count > 0:
            pois.append(POI(
                name=f"{count} {label} within 500m",
                category=cat,
                distance_m=500,
            ))

    return pois


# ── Transit stations with route badges ─────────────────────────

def get_transit_stations(lat: float, lon: float) -> list[TransitStation]:
    """Get nearby transit stations with route letters.

    Uses the GTFS data that's already loaded by the TransitScorer.
    We load it fresh here since the API process may not have the scorer
    instance available.
    """
    try:
        from apthunt.data.transit_data import TransitData, _haversine

        # Find the stops.txt file
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        stops_candidates = [
            os.path.join(base_dir, "data", "stops.txt"),
            os.path.join(base_dir, "data", "gtfs", "stops.txt"),
            os.path.join(base_dir, "apthunt", "data", "gtfs", "stops.txt"),
            os.path.join(base_dir, "gtfs", "stops.txt"),
        ]

        stops_path = None
        for p in stops_candidates:
            if os.path.exists(p):
                stops_path = p
                break

        if not stops_path:
            return []

        td = TransitData(stops_path)
        stations = td.stations_within(lat, lon, 800)

        result: list[TransitStation] = []
        for s in stations:
            dist = int(_haversine(lat, lon, s.lat, s.lon))
            result.append(TransitStation(
                name=s.name,
                distance_m=dist,
                routes=s.routes[:12],  # cap at 12 routes
            ))

        # Sort by distance
        result.sort(key=lambda x: x.distance_m)
        return result[:8]  # top 8 nearest stations

    except Exception:
        return []
