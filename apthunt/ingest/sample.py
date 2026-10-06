"""
Synthetic sample listings — the ``sample`` listing source.

Everything a listing says about itself here is invented: the street names
are made up (none appears in the city's PLUTO address list), and the
buildings, units, rents, concessions, price histories and descriptions
are drawn from a seeded random generator. No row is copied from, or
derived from, any real listing.

Two things are real, on purpose: the neighborhood names, and the rough
location of each neighborhood. The scorers work on coordinates, so a
synthetic listing dropped into Astoria is scored against Astoria's actual
open data (crime, 311, parks, subway entrances, ...), and building-level
scorers attach it to the nearest real tax lot.

Determinism: the same ``seed`` and ``count`` always produce the same
listings. Dates (first seen, availability, price history) are generated as
offsets from ``as_of``, which defaults to today so that freshness signals
such as "price cut 9 days ago" stay meaningful whenever the data is built.
Pin ``as_of`` to get byte-identical output across days.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterator

from apthunt.ingest import ListingSource, register_source

DEFAULT_SEED = 1811
DEFAULT_COUNT = 300
SOURCE_NAME = "sample"


# ── Neighborhoods ───────────────────────────────────────────────
#
# Hand-written, coarse parameters: a center point, a scatter radius kept
# small enough to stay on dry land, a typical one-bedroom rent, a relative
# share of listings, and how much of the housing stock is elevator /
# doorman buildings. They exist to make the sample look like a city, not
# to describe the market.

@dataclass(frozen=True)
class _Hood:
    name: str
    borough: str
    zip: str
    lat: float
    lon: float
    radius_m: int
    rent_1br: int
    weight: float
    highrise: float


_HOODS: tuple[_Hood, ...] = (
    _Hood("Financial District", "manhattan", "10005", 40.7075, -74.0090, 300, 4600, 1.2, 0.90),
    _Hood("East Village", "manhattan", "10009", 40.7265, -73.9830, 450, 4300, 1.3, 0.15),
    _Hood("Lower East Side", "manhattan", "10002", 40.7170, -73.9870, 400, 4200, 1.0, 0.25),
    _Hood("Chelsea", "manhattan", "10011", 40.7465, -74.0000, 450, 5000, 1.1, 0.50),
    _Hood("Hell's Kitchen", "manhattan", "10019", 40.7640, -73.9910, 400, 4400, 1.2, 0.50),
    _Hood("Murray Hill", "manhattan", "10016", 40.7480, -73.9780, 350, 4300, 0.9, 0.60),
    _Hood("Upper East Side", "manhattan", "10021", 40.7720, -73.9590, 500, 4100, 1.4, 0.50),
    _Hood("Upper West Side", "manhattan", "10024", 40.7870, -73.9750, 400, 4400, 1.3, 0.50),
    _Hood("Harlem", "manhattan", "10027", 40.8110, -73.9470, 500, 2900, 1.2, 0.20),
    _Hood("Washington Heights", "manhattan", "10032", 40.8440, -73.9390, 450, 2500, 1.0, 0.25),
    _Hood("Williamsburg", "brooklyn", "11211", 40.7140, -73.9570, 500, 4500, 1.5, 0.40),
    _Hood("Greenpoint", "brooklyn", "11222", 40.7290, -73.9520, 450, 4100, 1.0, 0.25),
    _Hood("Bushwick", "brooklyn", "11237", 40.6980, -73.9200, 500, 3100, 1.2, 0.10),
    _Hood("Bed-Stuy", "brooklyn", "11216", 40.6860, -73.9400, 600, 3000, 1.3, 0.08),
    _Hood("Crown Heights", "brooklyn", "11213", 40.6700, -73.9440, 550, 2900, 1.2, 0.15),
    _Hood("Park Slope", "brooklyn", "11215", 40.6720, -73.9800, 450, 3700, 1.1, 0.12),
    _Hood("Fort Greene", "brooklyn", "11205", 40.6865, -73.9745, 300, 3900, 0.8, 0.30),
    _Hood("Brooklyn Heights", "brooklyn", "11201", 40.6960, -73.9940, 300, 4400, 0.8, 0.30),
    _Hood("Sunset Park", "brooklyn", "11220", 40.6470, -74.0080, 450, 2500, 0.9, 0.05),
    _Hood("Flatbush", "brooklyn", "11226", 40.6410, -73.9600, 550, 2400, 1.0, 0.20),
    _Hood("Astoria", "queens", "11103", 40.7660, -73.9200, 550, 2900, 1.3, 0.15),
    _Hood("Long Island City", "queens", "11101", 40.7475, -73.9450, 350, 4200, 1.1, 0.80),
    _Hood("Sunnyside", "queens", "11104", 40.7440, -73.9210, 400, 2600, 0.8, 0.15),
    _Hood("Jackson Heights", "queens", "11372", 40.7520, -73.8850, 500, 2400, 0.9, 0.30),
    _Hood("Mott Haven", "bronx", "10454", 40.8090, -73.9230, 450, 2700, 0.8, 0.35),
    _Hood("Riverdale", "bronx", "10471", 40.8890, -73.9090, 450, 2400, 0.8, 0.45),
)

# Every (neighborhood, bedroom count) group that should be able to form a
# comp set gets at least this many listings (the deal scorer needs 3).
_GUARANTEED_BEDS = (0, 0, 0, 1, 1, 1, 1, 2, 2, 2)
_BED_WEIGHTS = ((0, 0.15), (1, 0.35), (2, 0.25), (3, 0.25))

_BED_RENT_MULT = {0: 0.80, 1: 1.00, 2: 1.42, 3: 1.90}
_BED_SQFT = {0: 430, 1: 640, 2: 900, 3: 1220}
_BED_LABEL = {0: "studio", 1: "one-bedroom", 2: "two-bedroom", 3: "three-bedroom"}

# ── Invented street names ───────────────────────────────────────

_NAME_HEADS = (
    "Alder", "Bramble", "Cinder", "Dapple", "Ember", "Fallow", "Gable",
    "Harrow", "Ivory", "Juniper", "Kestrel", "Larch", "Mallow", "Nettle",
    "Osprey", "Pember", "Quill", "Rowan", "Sorrel", "Thistle", "Umber",
    "Vesper", "Wren", "Yarrow", "Zephyr", "Birch", "Clover", "Dunlin",
    "Fennel", "Heron",
)
_NAME_TAILS = (
    "wick", "mere", "combe", "holt", "stead", "croft", "vale", "hurst",
    "field", "gate", "moor", "ford",
)
_STREET_TYPES = ("Street", "Street", "Avenue", "Place", "Row", "Mews", "Walk", "Terrace")

# ── Buildings ───────────────────────────────────────────────────

_KIND_PREMIUM = {"walkup": 1.00, "elevator": 1.07, "doorman": 1.16}

# (amenity, probability) — names line up with the amenity vocabulary the
# API categorizes and the unit-amenities scorer weights.
_BUILDING_AMENITIES: dict[str, tuple[tuple[str, float], ...]] = {
    "walkup": (
        ("Laundry in Building", 0.45), ("Live-in Super", 0.30), ("Bike Room", 0.15),
        ("Storage", 0.15), ("Courtyard", 0.10), ("Roof Deck", 0.08),
    ),
    "elevator": (
        ("Elevator", 1.0), ("Laundry in Building", 0.85), ("Live-in Super", 0.60),
        ("Bike Room", 0.50), ("Package Room", 0.40), ("Storage", 0.40),
        ("Roof Deck", 0.35), ("Gym", 0.30), ("Virtual Doorman", 0.30),
    ),
    "doorman": (
        ("Elevator", 1.0), ("Doorman", 1.0), ("Gym", 0.85), ("Package Room", 0.80),
        ("Bike Room", 0.80), ("Roof Deck", 0.70), ("Laundry in Building", 0.60),
        ("Residents Lounge", 0.55), ("Storage", 0.50), ("Parking", 0.35),
        ("Concierge", 0.30), ("Children's Playroom", 0.25), ("Pool", 0.15),
    ),
}
_UNIT_AMENITIES: dict[str, tuple[tuple[str, float], ...]] = {
    "walkup": (
        ("Hardwood Floors", 0.75), ("Dishwasher", 0.40), ("Stainless Steel Appliances", 0.40),
        ("High Ceilings", 0.30), ("Exposed Brick", 0.25), ("Washer/Dryer In Unit", 0.10),
        ("Private Outdoor Space", 0.08), ("Central Air", 0.06),
    ),
    "elevator": (
        ("Hardwood Floors", 0.70), ("Dishwasher", 0.65), ("Stainless Steel Appliances", 0.55),
        ("Central Air", 0.30), ("Washer/Dryer In Unit", 0.22), ("High Ceilings", 0.20),
        ("Walk-in Closet", 0.18), ("Balcony", 0.12),
    ),
    "doorman": (
        ("Dishwasher", 0.92), ("Stainless Steel Appliances", 0.80), ("Central Air", 0.75),
        ("Hardwood Floors", 0.60), ("Washer/Dryer In Unit", 0.50), ("Walk-in Closet", 0.30),
        ("City View", 0.30), ("Balcony", 0.22), ("Terrace", 0.05),
    ),
}

_OPENERS = (
    "Bright", "Sunny", "Quiet", "Renovated", "Spacious", "Corner",
    "Top-floor", "Rear-facing", "Loft-style", "Classic",
)
_DETAILS = (
    "oversized windows", "an updated kitchen", "generous closet space",
    "an open layout", "a windowed bath", "a separate dining nook",
    "new wide-plank floors", "a proper entry foyer", "good cross-ventilation",
    "room for a home office",
)
_BLOCKS = ("tree-lined", "quiet", "residential", "central", "low-traffic")
_DISCLAIMER = "Synthetic sample listing, generated for demonstration. Not a real apartment."


@dataclass
class _Building:
    address: str
    lat: float
    lon: float
    kind: str
    stories: int
    year: int
    amenities: list[str]
    taken_units: set[str]


def _round_to(value: float, step: int) -> int:
    return int(round(value / step) * step)


def _weighted(rng: random.Random, pairs) -> Any:
    roll = rng.random() * sum(w for _, w in pairs)
    for value, weight in pairs:
        roll -= weight
        if roll <= 0:
            return value
    return pairs[-1][0]


def _allocate(count: int) -> list[int]:
    """Split ``count`` listings across neighborhoods by weight (largest remainder)."""
    floor = len(_GUARANTEED_BEDS) if count >= len(_GUARANTEED_BEDS) * len(_HOODS) else 0
    spare = count - floor * len(_HOODS)
    total = sum(h.weight for h in _HOODS)
    exact = [spare * h.weight / total for h in _HOODS]
    sizes = [int(x) for x in exact]
    by_remainder = sorted(range(len(_HOODS)), key=lambda i: (-(exact[i] - sizes[i]), i))
    for i in by_remainder[: spare - sum(sizes)]:
        sizes[i] += 1
    return [floor + s for s in sizes]


class SampleSource(ListingSource):
    """Deterministic generator of fictional NYC rental listings."""

    name = SOURCE_NAME

    def __init__(
        self,
        seed: int = DEFAULT_SEED,
        count: int = DEFAULT_COUNT,
        as_of: date | str | None = None,
    ):
        self.seed = int(seed)
        self.count = int(count)
        if self.count < 1:
            raise ValueError("count must be positive")
        if as_of is None:
            as_of = datetime.now(timezone.utc).date()
        elif isinstance(as_of, str):
            as_of = date.fromisoformat(as_of)
        self.as_of: date = as_of

    # ── public ──────────────────────────────────────────────────

    def fetch(self) -> Iterator[dict[str, Any]]:
        rng = random.Random(self.seed)
        street_names = [h + t for h in _NAME_HEADS for t in _NAME_TAILS]
        rng.shuffle(street_names)
        index = 0
        for hood, size in zip(_HOODS, _allocate(self.count)):
            if size == 0:
                continue
            # A handful of streets per neighborhood, never shared between them.
            streets = [
                f"{street_names.pop()} {rng.choice(_STREET_TYPES)}"
                for _ in range(min(6, max(2, size // 2)))
            ]
            buildings = self._buildings(rng, hood, size, streets)
            beds = list(_GUARANTEED_BEDS) if size >= len(_GUARANTEED_BEDS) else []
            beds += [_weighted(rng, _BED_WEIGHTS) for _ in range(size - len(beds))]
            rng.shuffle(beds)
            for n_beds in beds:
                index += 1
                yield self._listing(rng, index, hood, rng.choice(buildings), n_beds)

    # ── buildings ───────────────────────────────────────────────

    def _buildings(
        self, rng: random.Random, hood: _Hood, size: int, streets: list[str]
    ) -> list[_Building]:
        buildings: list[_Building] = []
        used: set[str] = set()
        top_number = max(398, size)  # always more addresses than buildings
        for _ in range(max(1, math.ceil(size * 0.8))):
            while True:
                address = f"{rng.randint(2, top_number)} {rng.choice(streets)}"
                if address not in used:
                    used.add(address)
                    break

            # Uniform scatter inside the neighborhood disc.
            bearing = rng.uniform(0, 2 * math.pi)
            dist = hood.radius_m * math.sqrt(rng.random())
            lat = hood.lat + (dist * math.cos(bearing)) / 111_320
            lon = hood.lon + (dist * math.sin(bearing)) / (
                111_320 * math.cos(math.radians(hood.lat))
            )

            if rng.random() < hood.highrise:
                kind = "doorman" if rng.random() < 0.45 else "elevator"
            else:
                kind = "walkup"
            if kind == "walkup":
                stories, year = rng.randint(3, 6), rng.randint(1895, 1940)
            elif kind == "elevator":
                stories, year = rng.randint(6, 16), rng.randint(1925, 2012)
            else:
                stories, year = rng.randint(12, 42), rng.randint(1986, 2023)

            amenities = [a for a, p in _BUILDING_AMENITIES[kind] if rng.random() < p]
            buildings.append(
                _Building(address, round(lat, 6), round(lon, 6), kind, stories, year,
                          amenities, set())
            )
        return buildings

    # ── one listing ─────────────────────────────────────────────

    def _listing(
        self, rng: random.Random, index: int, hood: _Hood, bld: _Building, beds: int
    ) -> dict[str, Any]:
        as_of = self.as_of

        # Unit label, unique within the building.
        letters = "ABCD" if bld.kind == "walkup" else "ABCDEFGH"
        for attempt in range(200):
            floor = rng.randint(1, bld.stories)
            unit = f"{floor}{rng.choice(letters)}"
            if attempt >= 100:  # a very full building: fall back to rear units
                unit += "-R"
            if unit not in bld.taken_units:
                break
        bld.taken_units.add(unit)

        # Rent: neighborhood level x size x building type x unit-level noise,
        # with a few deliberate bargains and stretches so deal scores spread.
        noise = rng.gauss(0, 0.09)
        roll = rng.random()
        if roll < 0.08:
            noise -= rng.uniform(0.10, 0.20)
        elif roll > 0.94:
            noise += rng.uniform(0.10, 0.22)
        view_premium = 1.0 if bld.kind == "walkup" else 1 + 0.004 * min(floor, 25)
        price = _round_to(
            hood.rent_1br * _BED_RENT_MULT[beds] * _KIND_PREMIUM[bld.kind]
            * view_premium * math.exp(noise),
            25,
        )

        sqft = None
        if rng.random() < 0.72:
            sqft = _round_to(
                _BED_SQFT[beds] * math.exp(0.45 * noise + rng.gauss(0, 0.09))
                * (1.08 if bld.kind == "doorman" else 1.0),
                5,
            )

        if beds <= 1:
            baths = 1.0
        elif beds == 2:
            baths = _weighted(rng, ((1.0, 0.55), (1.5, 0.12), (2.0, 0.33)))
        else:
            baths = _weighted(rng, ((1.0, 0.15), (1.5, 0.15), (2.0, 0.55), (2.5, 0.15)))

        # Concession: months free on a longer lease → lower net effective rent.
        months_free = lease_term = net_effective = None
        if rng.random() < 0.18:
            months_free = rng.choice((1.0, 1.0, 1.5, 2.0))
            lease_term = rng.choice((13, 14, 15))
            net_effective = int(round(price * (lease_term - months_free) / lease_term))
        else:
            lease_term = 12

        # Days on market, and an optional price change since listing.
        days_on_market = 1 + min(int(rng.expovariate(1 / 16)), 120)
        price_delta = 0
        changed_on: date | None = None
        roll = rng.random()
        if roll < 0.22:      # cut
            previous = _round_to(price / (1 - rng.uniform(0.02, 0.08)), 25)
            price_delta = price - max(previous, price + 25)
        elif roll < 0.27:    # increase
            price_delta = _round_to(price * rng.uniform(0.01, 0.04), 25) or 25
        if price_delta:
            since_change = rng.randint(1, 60)
            changed_on = as_of - timedelta(days=since_change)
            days_on_market = since_change + rng.randint(5, 40)
        listed_on = as_of - timedelta(days=days_on_market)
        first_seen = datetime.combine(
            listed_on, time(rng.randint(7, 21), rng.randint(0, 59)), tzinfo=timezone.utc
        )

        available_at = as_of + timedelta(days=rng.randint(-10, 50))
        if rng.random() < 0.25:
            available_at = min(available_at, as_of)

        # Amenities — some listings simply don't publish any.
        amenities: list[str] | None = None
        pets_allowed: bool | None = None
        if rng.random() < 0.86:
            amenities = list(bld.amenities) + [
                a for a, p in _UNIT_AMENITIES[bld.kind] if rng.random() < p
            ]
            pet_roll = rng.random()
            if pet_roll < 0.42:
                amenities.append("Pets Allowed")
                pets_allowed = True
            elif pet_roll < 0.56:
                amenities.append("Cats Allowed")
                pets_allowed = True
            elif pet_roll < 0.70:
                pets_allowed = False
        furnished = rng.random() < 0.04
        if furnished and amenities is not None:
            amenities.append("Furnished")

        description = (
            f"{rng.choice(_OPENERS)} {_BED_LABEL[beds]} with {rng.choice(_DETAILS)}"
            f" on a {rng.choice(_BLOCKS)} block in {hood.name}. {_DISCLAIMER}"
        )

        history = self._price_history(
            rng, price, price_delta, listed_on, changed_on
        )

        return {
            "source_id": f"{SOURCE_NAME}-{index:04d}",
            "url": None,
            "address": bld.address,
            "unit": unit,
            "neighborhood": hood.name,
            "borough": hood.borough,
            "zip": hood.zip,
            "lat": bld.lat,
            "lon": bld.lon,
            "price": price,
            "net_effective_price": net_effective,
            "no_fee": rng.random() < 0.55,
            "months_free": months_free,
            "lease_term_months": lease_term,
            "beds": beds,
            "baths": baths,
            "sqft": sqft,
            "amenities": amenities,
            "pets_allowed": pets_allowed,
            "furnished": furnished,
            "description": description,
            "photos": None,
            "available_at": available_at.isoformat(),
            "broker_name": None,
            "broker_firm": None,
            "broker_phone": None,
            "broker_email": None,
            "price_history": history or None,
            "relist_count": sum(1 for e in history if e["event"] == "Listed") or None,
            "building_year": bld.year,
            "building_stories": bld.stories,
            "first_seen_at": first_seen.isoformat(),
            # The source payload. The deal scorer reads its leverage signals
            # (price_delta / price_changed_at / months_free) from here.
            "raw_json": {
                "synthetic": True,
                "generator": "apthunt.ingest.sample",
                "seed": self.seed,
                "building_kind": bld.kind,
                "price_delta": price_delta,
                "price_changed_at": changed_on.isoformat() if changed_on else None,
                "months_free": months_free or 0,
            },
        }

    # ── price history ───────────────────────────────────────────

    @staticmethod
    def _price_history(
        rng: random.Random,
        price: int,
        price_delta: int,
        listed_on: date,
        changed_on: date | None,
    ) -> list[dict[str, str]]:
        """Listing cycles, newest first: [{date, price, event}, ...].

        Roughly half the listings carry history. Earlier cycles are spaced
        by a tenancy plus a vacancy, which is what the deal scorer's tenure
        estimate reads; a few cycles include a rapid relist so that its
        clustering rule has something to cluster.
        """
        if not price_delta and rng.random() > 0.45:
            return []

        def money(amount: int) -> str:
            return f"${amount:,}"

        events: list[tuple[date, int, str]] = []
        opening = price - price_delta
        events.append((listed_on, opening, "Listed"))
        if price_delta and changed_on:
            events.append(
                (changed_on, price, "Price decreased" if price_delta < 0 else "Price increased")
            )

        cursor, rent = listed_on, opening
        for _ in range(_weighted(rng, ((0, 0.50), (1, 0.27), (2, 0.15), (3, 0.08)))):
            tenancy_months = _weighted(
                rng, ((rng.randint(4, 9), 0.2), (rng.randint(11, 14), 0.5), (rng.randint(20, 40), 0.3))
            )
            vacancy = rng.randint(10, 45)
            earlier = cursor - timedelta(days=int(tenancy_months * 30.44) + vacancy)
            rent = _round_to(rent / (1 + rng.uniform(0.02, 0.05)) ** (tenancy_months / 12), 25)
            events.append((earlier, rent, "Listed"))
            signed_at = rent
            if rng.random() < 0.25:  # pulled and relisted a little lower
                signed_at = rent - 50
                events.append(
                    (earlier + timedelta(days=rng.randint(3, vacancy - 2)), signed_at, "Listed")
                )
            events.append((earlier + timedelta(days=vacancy), signed_at, "Rented"))
            cursor = earlier

        events.sort(key=lambda e: e[0], reverse=True)
        return [
            {"date": d.isoformat(), "price": money(p), "event": event}
            for d, p, event in events
        ]


register_source(SOURCE_NAME, SampleSource)
