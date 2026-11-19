"""Place hierarchy facts used by the 19 Nov 2026 tree cleanup (Trevor's rule, t61u):
full names as place > city > county/district > state/province > country.

Only facts that are certain go here: a state/province/country stated in the chart text,
or a town whose identity is unambiguous (a name that exists in only one county of its
state, or a city that is unmistakable in this tree, e.g. Philadelphia written "Phila." on
Philadelphia death certificates). Anything else is left without a county (or without a
state) and reported as flagged for Trevor.
"""

US_STATES = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
    "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
    "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
    "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico",
    "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont",
    "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
}
CA_PROVINCES = {"Québec", "New Brunswick", "Nova Scotia", "Ontario", "Prince Edward Island",
                "Manitoba", "Saskatchewan", "Alberta", "British Columbia"}
COUNTRIES = {"United States", "Canada", "England", "Ireland", "France", "Germany", "Scotland",
             "Switzerland", "Crimea", "Acadie", "Russia"}

# Spelling / abbreviation clean-up of a single name (lower-case key)
ALIASES = {
    "w. springfield": "West Springfield", "phila": "Philadelphia", "phila.": "Philadelphia",
    "bklyn": "Brooklyn", "manh": "Manhattan", "manh.": "Manhattan", "london": "London",
    "contrecoeur": "Contrecœur", "beloeil": "Belœil", "st-frs-du-lac": "St-François-du-Lac",
    "i.o": "Île d'Orléans", "i.o.": "Île d'Orléans", "î.o": "Île d'Orléans", "î.o.": "Île d'Orléans",
    "mandan union cem": "Mandan Union Cemetery", "mandan union cem.": "Mandan Union Cemetery",
    "gilpin twp": "Gilpin Township", "gilpin twp.": "Gilpin Township",
    "bethel twp": "Bethel Township", "bethel twp.": "Bethel Township",
    "kiski township": "Kiskiminetas Township", "kiski twp": "Kiskiminetas Township",
    "mt. carroll": "Mount Carroll", "armstrong co": "Armstrong County", "armstrong co.": "Armstrong County",
    "mercer co": "Mercer County", "mercer co.": "Mercer County", "grant co": "Grant County",
    "grant co.": "Grant County", "king co": "King County", "king co.": "King County",
    "wash. co": "Washington County", "wash. co.": "Washington County",
    "co. armagh": "County Armagh", "monaghan": "County Monaghan", "bucks": "Buckinghamshire",
    "bucks.": "Buckinghamshire", "quebec": "Québec",
}

# Towns written WITHOUT a state/country in the chart, whose identity is certain:
# name -> (country, state/province or None, county/department or None)
UNQUALIFIED = {
    # Pennsylvania (Armstrong / Indiana / Westmoreland / Lancaster County families)
    "Philadelphia": ("United States", "Pennsylvania", "Philadelphia County"),
    "Apollo": ("United States", "Pennsylvania", "Armstrong County"),
    "Kiskiminetas Township": ("United States", "Pennsylvania", "Armstrong County"),
    "Gilpin Township": ("United States", "Pennsylvania", "Armstrong County"),
    "Elderton": ("United States", "Pennsylvania", "Armstrong County"),
    "Leechburg": ("United States", "Pennsylvania", "Armstrong County"),
    "Ford City": ("United States", "Pennsylvania", "Armstrong County"),
    "Girty": ("United States", "Pennsylvania", "Armstrong County"),
    "Spring Church Joint Cemetery": ("United States", "Pennsylvania", "Armstrong County"),
    "Shelocta": ("United States", "Pennsylvania", "Indiana County"),
    "Plumville": ("United States", "Pennsylvania", "Indiana County"),
    "Saltsburg": ("United States", "Pennsylvania", "Indiana County"),
    "Vandergrift Cemetery": ("United States", "Pennsylvania", "Westmoreland County"),
    "Rohrerstown": ("United States", "Pennsylvania", "Lancaster County"),
    # New York City boroughs
    "Brooklyn": ("United States", "New York", "Kings County"),
    "New Lots": ("United States", "New York", "Kings County"),
    "Manhattan": ("United States", "New York", "New York County"),
    # Washington / North Dakota / Connecticut
    "Seattle": ("United States", "Washington", "King County"),
    "Yakima": ("United States", "Washington", "Yakima County"),
    "Mandan Union Cemetery": ("United States", "North Dakota", "Morton County"),
    "Killingly": ("United States", "Connecticut", "Windham County"),
    # Québec (names that exist only in Québec)
    **{n: ("Canada", "Québec", None) for n in [
        "Verchères", "Contrecœur", "Yamaska", "Belœil", "St-Ours", "St-François-du-Lac",
        "St-Hyacinthe", "Grondines", "Boucherville", "Beauport", "Montréal", "Trois-Rivières",
        "Batiscan", "Château-Richer", "Cap-St-Ignace", "L'Ange-Gardien", "Ste-Anne-de-la-Pérade",
        "Rivière-Ouelle", "Rivière-des-Prairies", "Île Jésus", "Pointe-aux-Trembles",
        "Pointe-du-Lac", "Cap-de-la-Madeleine", "Sorel", "La Prairie", "St-Charles-de-Bellechasse",
        "Île d'Orléans", "Beaupré", "St-Jude", "St-Damase"]},
    # Acadia / Nova Scotia / New Brunswick
    "Grand-Pré": ("Canada", "Nova Scotia", None),
    "Annapolis Royal": ("Canada", "Nova Scotia", None),
    "Les Mines": ("Canada", "Nova Scotia", None),
    "Memramcook": ("Canada", "New Brunswick", "Westmorland County"),
    # France (department as the district level)
    "Paris": ("France", None, None),
    "La Rochelle": ("France", None, "Charente-Maritime"),
    "Brouage": ("France", None, "Charente-Maritime"),
    "St-Xandre": ("France", None, "Charente-Maritime"),
    "Niort": ("France", None, "Deux-Sèvres"),
    "Maulais": ("France", None, "Deux-Sèvres"),        # chart: "(Deux-Sèvres)"
    "Tessonnière": ("France", None, "Deux-Sèvres"),    # chart: "(Deux-Sèvres)"
    "Dieppe": ("France", None, "Seine-Maritime"),
    "Ectot-lès-Baons": ("France", None, "Seine-Maritime"),
    "Honfleur": ("France", None, "Calvados"),
    "Touques": ("France", None, "Calvados"),
    "Combray": ("France", None, "Calvados"),           # chart: "(Calvados)"
    "Nancy": ("France", None, "Meurthe-et-Moselle"),
    "Tourouvre": ("France", None, "Orne"),
    "Chanu": ("France", None, "Orne"),
    "Angers": ("France", None, "Maine-et-Loire"),
    "Sables-d'Olonne": ("France", None, "Vendée"),
    "La Roche-sur-Yon": ("France", None, "Vendée"),
    "St-Germain-de-Prinçay": ("France", None, "Vendée"),
    "Les Roches-Baritaud": ("France", None, "Vendée"),
    "Vitry-le-François": ("France", None, "Marne"),
    "Panzoult": ("France", None, "Indre-et-Loire"),
    "St-Illiers-la-Ville": ("France", None, "Yvelines"),  # chart: "(Yvelines)"
    "St-Amant-de-Boixe": ("France", None, "Charente"),
    "Cissé": ("France", None, "Vienne"),
    "Couhé": ("France", None, "Vienne"),
    "Blanzac": ("France", None, "Haute-Vienne"),       # chart: "(Haute-Vienne)"
    "Etupes": ("France", None, "Doubs"),
    "Anjou": ("France", None, None),
    # British Isles
    "Rastrick": ("England", None, "Yorkshire"),
    "Brighouse chapel": ("England", None, "Yorkshire"),
    "Aston Abbotts": ("England", None, "Buckinghamshire"),
    "Wingrave": ("England", None, "Buckinghamshire"),
    "Wimborne Minster": ("England", None, "Dorset"),
    "London": ("England", None, None),
    "Castleblayney": ("Ireland", None, "County Monaghan"),
    "County Armagh": ("Ireland", None, None),
    "County Derry": ("Ireland", None, None),
    "County Monaghan": ("Ireland", None, None),
    "Yorkshire": ("England", None, None),
    "Buckinghamshire": ("England", None, None),
    # Elsewhere
    "Fribourg": ("Switzerland", None, None),           # chart: "(Swiss)"
    "Sudak": ("Crimea", None, None),
    "Bavaria": ("Germany", None, None),
}
UNQUALIFIED.update({
    "King County": ("United States", "Washington", None),   # 1898 King County, Washington marriage record
    "Normandie": ("France", None, None), "Poitou": ("France", None, None),
})
# A burial ground or sub-place whose town is certain from the records in this tree
PARENT_TOWN = {
    "Washelli Cemetery": "Seattle",          # death certificate: burial Washelli, Seattle
    "Elderton Cemetery": "Elderton",
    "Forks-Zion": "Gilpin Township",         # chart: "bur. Forks-Zion, Gilpin Twp."
}
# Historical French provinces in the chart text: kept in the event description, dropped
# from the hierarchy when the town itself is known (department used instead)
DROP_IF_TOWN_KNOWN = {"Normandie", "Poitou", "Aunis", "Périgord"}
# Bavaria is a state of Germany; Yorkshire etc. are counties; mark the level explicitly
LEVEL_OF = {"Bavaria": "State", "Yorkshire": "County", "Buckinghamshire": "County",
            "County Armagh": "County", "County Derry": "County", "County Monaghan": "County",
            "Anjou": "Province", "Normandie": "Province", "Poitou": "Province"}
ALIASES.update({"washelli": "Washelli Cemetery", "kiskiminetas twp": "Kiskiminetas Township",
                "kiskiminetas twp.": "Kiskiminetas Township"})

# County for a town whose state is known (only towns that exist in one county of that state)
COUNTY = {
    ("Springfield", "Massachusetts"): "Hampden County", ("West Springfield", "Massachusetts"): "Hampden County",
    ("Holyoke", "Massachusetts"): "Hampden County", ("Palmer", "Massachusetts"): "Hampden County",
    ("Deerfield", "Massachusetts"): "Franklin County",
    ("Clinton", "Massachusetts"): "Worcester County", ("Boylston", "Massachusetts"): "Worcester County",
    ("Lancaster", "Massachusetts"): "Worcester County", ("Charlton", "Massachusetts"): "Worcester County",
    ("Worcester", "Massachusetts"): "Worcester County", ("Holden", "Massachusetts"): "Worcester County",
    ("Grafton", "Massachusetts"): "Worcester County", ("North Brookfield", "Massachusetts"): "Worcester County",
    ("Medway", "Massachusetts"): "Norfolk County", ("Needham", "Massachusetts"): "Norfolk County",
    ("Hartford", "Connecticut"): "Hartford County", ("Killingly", "Connecticut"): "Windham County",
    ("Thompson", "Connecticut"): "Windham County", ("Norwalk", "Connecticut"): "Fairfield County",
    ("Franklin", "New Hampshire"): "Merrimack County",
    ("Strafford", "Vermont"): "Orange County", ("Newfane", "Vermont"): "Windham County",
    ("Orwell", "Vermont"): "Addison County", ("Cornwall", "Vermont"): "Addison County",
    ("New Haven", "Vermont"): "Addison County",
    ("Peru", "New York"): "Clinton County", ("Northport", "New York"): "Suffolk County",
    ("Brooklyn", "New York"): "Kings County",
    ("Seattle", "Washington"): "King County", ("Kent", "Washington"): "King County",
    ("Yakima", "Washington"): "Yakima County",
    ("Beulah", "North Dakota"): "Mercer County", ("Hazen", "North Dakota"): "Mercer County",
    ("Zap", "North Dakota"): "Mercer County", ("Jamestown", "North Dakota"): "Stutsman County",
    ("Mandan", "North Dakota"): "Morton County",
    ("Post Falls", "Idaho"): "Kootenai County", ("Menno", "South Dakota"): "Hutchinson County",
    ("Mount Carroll", "Illinois"): "Carroll County", ("Lostant", "Illinois"): "LaSalle County",
    ("Marquette", "Michigan"): "Marquette County",
    ("Philadelphia", "Pennsylvania"): "Philadelphia County",
    ("Apollo", "Pennsylvania"): "Armstrong County", ("Kiskiminetas Township", "Pennsylvania"): "Armstrong County",
    ("Gilpin Township", "Pennsylvania"): "Armstrong County", ("Lower Burrell", "Pennsylvania"): "Westmoreland County",
    ("Memramcook", "New Brunswick"): "Westmorland County", ("Grande-Digue", "New Brunswick"): "Kent County",
    ("Falmouth", "Nova Scotia"): "Hants County",
}
# Independent cities (no county by law)
NO_COUNTY_OK = {("Hampton", "Virginia")}
