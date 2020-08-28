"""Download geometries from OpenStreetMap."""

import logging as lg

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString
from shapely.geometry import MultiPolygon
from shapely.geometry import Point
from shapely.geometry import Polygon
from shapely.geos import TopologicalError
from shapely.ops import linemerge
from shapely.ops import polygonize

from . import downloader
from . import geocoder
from . import settings
from . import utils
from . import utils_geo
from ._errors import EmptyOverpassResponse
from .polygon_features import polygon_features


def gdf_from_bbox(north, south, east, west, tags):
    """
    Create a geodataframe of OSM geometries within a N, S, E, W bounding box.

    Parameters
    ----------
    north : float
        northern latitude of bounding box
    south : float
        southern latitude of bounding box
    east : float
        eastern longitude of bounding box
    west : float
        western longitude of bounding box
    tags : dict
        Dict of tags used for finding geometry from the selected area. Results
        returned are the union, not intersection of each individual tag.
        Each result matches at least one tag given. The dict keys should be
        OSM tags, (e.g., `amenity`, `landuse`, `highway`, etc) and the dict
        values should be either `True` to retrieve all items with the given
        tag, or a string to get a single tag-value combination, or a list of
        strings to get multiple values for the given tag. For example,
        `tags = {'amenity':True, 'landuse':['retail','commercial'],
        'highway':'bus_stop'}` would return all amenities, landuse=retail,
        landuse=commercial, and highway=bus_stop.

    Returns
    -------
    gdf : geopandas.GeoDataFrame

    Notes
    -----
    You can configure the Overpass server timeout, memory allocation, and
    other custom settings via ox.config().
    """
    # convert bounding box to a polygon
    polygon = utils_geo.bbox_to_poly(north, south, east, west)

    # create geodataframe of geometries within this polygon
    gdf = gdf_from_polygon(polygon, tags)

    utils.log(f"gdf_from_bbox returned a geodataframe with {len(gdf)} geometries")
    return gdf


def gdf_from_point(center_point, tags, dist=1000):
    """
    Create a geodataframe of OSM geometries within some distance N, S, E, W of a (lat-lng) point.

    Parameters
    ----------
    center_point : tuple
        the (lat, lng) center point around which to get the geometries
    tags : dict
        Dict of tags used for finding geometry from the selected area. Results
        returned are the union, not intersection of each individual tag.
        Each result matches at least one tag given. The dict keys should be
        OSM tags, (e.g., `amenity`, `landuse`, `highway`, etc) and the dict
        values should be either `True` to retrieve all items with the given
        tag, or a string to get a single tag-value combination, or a list of
        strings to get multiple values for the given tag. For example,
        `tags = {'amenity':True, 'landuse':['retail','commercial'],
        'highway':'bus_stop'}` would return all amenities, landuse=retail,
        landuse=commercial, and highway=bus_stop.
    dist : numeric
        distance in meters

    Returns
    -------
    gdf : geopandas.GeoDataFrame

    Notes
    -----
    You can configure the Overpass server timeout, memory allocation, and
    other custom settings via ox.config().
    """
    # create a bounding box from the center point and the distance in each
    # direction
    north, south, east, west = utils_geo.bbox_from_point(center_point, dist)

    # convert the bounding box to a polygon
    polygon = utils_geo.bbox_to_poly(north, south, east, west)

    # create geodataframe of geometries within this polygon
    gdf = gdf_from_polygon(polygon, tags)

    utils.log(f"gdf_from_point returned a geodataframe with {len(gdf)} geometries")
    return gdf


def gdf_from_address(address, tags, dist=1000):
    """
    Create a geodataframe of OSM geometries within some distance N, S, E, W of an address.

    Parameters
    ----------
    address : string
        the address to geocode and use as the central point around which to
        get the geometries
    tags : dict
        Dict of tags used for finding geometry in the selected area. Results
        returned are the union, not intersection of each individual tag.
        Each result matches at least one tag given. The dict keys should be
        OSM tags, (e.g., `amenity`, `landuse`, `highway`, etc) and the dict
        values should be either `True` to retrieve all items with the given
        tag, or a string to get a single tag-value combination, or a list of
        strings to get multiple values for the given tag. For example,
        `tags = {'amenity':True, 'landuse':['retail','commercial'],
        'highway':'bus_stop'}` would return all amenities, landuse=retail,
        landuse=commercial, and highway=bus_stop.
    dist : numeric
        distance in meters

    Returns
    -------
    gdf : geopandas.GeoDataFrame

    Notes
    -----
    You can configure the Overpass server timeout, memory allocation, and
    other custom settings via ox.config().
    """
    # geocode the address string to a (lat, lng) point
    center_point = geocoder.geocode(query=address)

    # create geodataframe of geometries around this point
    gdf = gdf_from_point(center_point, tags, dist=dist)

    utils.log(f"gdf_from_address returned a geodataframe with {len(gdf)} geometries")
    return gdf


def gdf_from_place(query, tags, which_result=None, buffer_dist=None):
    """
    Create a geodataframe of OSM geometries within the boundaries of a place.

    Parameters
    ----------
    query : string or dict or list
        the query or queries to geocode to get place boundary polygon(s)
    tags : dict
        Dict of tags used for finding geometry in the selected area. Results
        returned are the union, not intersection of each individual tag.
        Each result matches at least one tag given. The dict keys should be
        OSM tags, (e.g., `amenity`, `landuse`, `highway`, etc) and the dict
        values should be either `True` to retrieve all items with the given
        tag, or a string to get a single tag-value combination, or a list of
        strings to get multiple values for the given tag. For example,
        `tags = {'amenity':True, 'landuse':['retail','commercial'],
        'highway':'bus_stop'}` would return all amenities, landuse=retail,
        landuse=commercial, and highway=bus_stop.
    which_result : int
        which geocoding result to use. if None, auto-select the first
        multi/polygon or raise an error if OSM doesn't return one.
    buffer_dist : float
        distance to buffer around the place geometry, in meters

    Returns
    -------
    gdf : geopandas.GeoDataFrame

    Notes
    -----
    You can configure the Overpass server timeout, memory allocation, and
    other custom settings via ox.config().
    """
    # create a GeoDataFrame with the spatial boundaries of the place(s)
    if isinstance(query, (str, dict)):
        # if it is a string (place name) or dict (structured place query), then
        # it is a single place
        gdf_place = geocoder.geocode_to_gdf(
            query, which_result=which_result, buffer_dist=buffer_dist
        )
    elif isinstance(query, list):
        # if it is a list, it contains multiple places to get
        gdf_place = geocoder.geocode_to_gdf(query, buffer_dist=buffer_dist)
    else:
        raise TypeError("query must be dict, string, or list of strings")

    # extract the geometry from the GeoDataFrame to use in API query
    polygon = gdf_place["geometry"].unary_union
    utils.log("Constructed place geometry polygon(s) to query API")

    # create geodataframe using this polygon(s) geometry
    gdf = gdf_from_polygon(polygon, tags)

    utils.log(f"gdf_from_place returned geodataframe with {len(gdf)} geometries")
    return gdf


def gdf_from_polygon(polygon, tags):
    """
    Create a geodataframe of OSM geometries within the boundaries of some shapely polygon.

    Parameters
    ----------
    polygon : shapely.geometry.Polygon
        geographic boundaries to fetch geometries within
    tags : dict
        Dict of tags used for finding POIs from the selected area. Results
        returned are the union, not intersection of each individual tag.
        Each result matches at least one tag given. The dict keys should be
        OSM tags, (e.g., `amenity`, `landuse`, `highway`, etc) and the dict
        values should be either `True` to retrieve all items with the given
        tag, or a string to get a single tag-value combination, or a list of
        strings to get multiple values for the given tag. For example,
        `tags = {'amenity':True, 'landuse':['retail','commercial'],
        'highway':'bus_stop'}` would return all amenities, landuse=retail,
        landuse=commercial, and highway=bus_stop.

    Returns
    -------
    gdf : geopandas.GeoDataFrame

    Notes
    -----
    You can configure the Overpass server timeout, memory allocation, and
    other custom settings via ox.config().
    """
    # verify that the geometry is valid and is a shapely Polygon/MultiPolygon
    # before proceeding
    if not polygon.is_valid:
        raise ValueError("The geometry to query within is invalid")
    if not isinstance(polygon, (Polygon, MultiPolygon)):
        raise TypeError(
            "Geometry must be a shapely Polygon or MultiPolygon. If you requested "
            "graph from place name, make sure your query resolves to a Polygon or "
            "MultiPolygon, and not some other geometry, like a Point. See OSMnx "
            "documentation for details."
        )

    # download the geometry data from OSM
    response_jsons = downloader._osm_geometry_download(polygon, tags)

    # create geodataframe from the downloaded data
    gdf = _create_gdf(response_jsons, polygon, tags)

    utils.log(f"gdf_from_polygon returned geodataframe with {len(gdf)} geometries")
    return gdf


def gdf_from_xml(filepath, polygon=None, tags=None):
    """
    Create a geodataframe of OSM geometries from an OSM-formatted XML file.

    Because this function creates a geodataframe of geometries from an
    OSM-formatted XML file that has already been downloaded (i.e. no query
    is made to the Overpass API) the polygon and tags arguments are not required.
    If they are not supplied to the function, gdf_from_xml() will return geometries
    for all of the tagged elements in the file. If they are supplied they will be
    used to filter the final geodataframe.

    Parameters
    ----------
    filepath : string
        path to file containing OSM XML data
    polygon : shapely.geometry.Polygon
        optional geographic boundary to filter geometries within
    tags : dict
        optional dict of tags used for filtering geometries from the XML. Results
        returned are the union, not intersection of each individual tag.
        Each result matches at least one tag given. The dict keys should be
        OSM tags, (e.g., `amenity`, `landuse`, `highway`, etc) and the dict
        values should be either `True` to retrieve all items with the given
        tag, or a string to get a single tag-value combination, or a list of
        strings to get multiple values for the given tag. For example,
        `tags = {'amenity':True, 'landuse':['retail','commercial'],
        'highway':'bus_stop'}` would return all amenities, landuse=retail,
        landuse=commercial, and highway=bus_stop.

    Returns
    -------
    gdf : geopandas.GeoDataFrame
    """
    # transmogrify file of OSM XML data into JSON
    response_jsons = [downloader._overpass_json_from_file(filepath)]

    # create geodataframe using this response JSON
    gdf = _create_gdf(response_jsons, polygon=polygon, tags=tags)

    utils.log(f"gdf_from_xml returned a geodataframe with {len(gdf)} geometries")
    return gdf


def _create_gdf(response_jsons, polygon, tags):
    """
    Parse JSON responses from the Overpass API to a GeoDataFrame.

    Note: the `polygon` and `tags` arguments can both be `None` and
    the geodataframe will still be created but it won't be filtered
    at the end i.e. the final geodataframe will contain all tagged
    geometries in the `response_jsons`.

    Parameters
    ----------
    response_jsons : list
        list of JSON responses from from the Overpass API
    polygon : shapely.geometry.Polygon
        geographic boundary used for filtering the final geodataframe
    tags : dict
        dict of tags used for filtering the final geodataframe

    Returns
    -------
    gdf : geopandas.GeoDataFrame
        geometries and their associated tags
    """
    utils.log("Creating geodataframe from downloaded OSM data...")

    # make sure we got data back from the server requests
    elements = []
    for response_json in response_jsons:
        elements.extend(response_json["elements"])
    if len(elements) < 1:
        raise EmptyOverpassResponse("There are no data elements in the response JSON")

    # Dictionaries to hold nodes and complete geometries
    coords = {}
    geometries = {}

    # Set to hold the unique IDs of elements that do not have tags
    untagged_element_ids = set()

    # extract geometries from the downloaded osm data
    for response_json in response_jsons:
        # Parses the JSON of OSM nodes, ways and (multipolygon) relations to dictionaries of
        # coordinates, Shapely Points, LineStrings, Polygons and MultiPolygons
        for element in response_json["elements"]:

            # id numbers are only unique within element types
            # create unique id from combination of type and id
            unique_id = f"{element['type']}/{element['id']}"

            # add elements that are not nodes and that are without tags or with empty tags
            # to the untagged_element_ids set (untagged nodes are not added to the geometries
            # dictionary at all)
            if (element["type"] != "node") & (("tags" not in element) or (not element["tags"])):
                untagged_element_ids.add(unique_id)

            if element["type"] == "node":
                # Parse all nodes to coords
                coord = _parse_node_to_coord(element=element)
                coords[element["id"]] = coord
                # If the node has tags and the tags are not empty parse it to a Point
                # Empty check is necessary for JSONs created from XML where nodes without tags
                # are assigned tags = {}
                if "tags" in element and element["tags"]:
                    point = _parse_node_to_point(element=element)
                    geometries[unique_id] = point

            elif element["type"] == "way":
                # Parse all ways to linestrings or polygons
                linestring_or_polygon = _parse_way_to_linestring_or_polygon(
                    element=element, coords=coords, polygon_features=polygon_features,
                )
                if linestring_or_polygon:
                    geometries[unique_id] = linestring_or_polygon

            elif (
                element["type"] == "relation" and element.get("tags").get("type") == "multipolygon"
            ):
                # Parse all multipolygon relations to multipolygons
                multipolygon = _parse_relation_to_multipolygon(
                    element=element, geometries=geometries
                )
                if multipolygon:
                    geometries[unique_id] = multipolygon

    utils.log(f"{len(geometries)} geometries in the dictionary")
    utils.log(f"{len(untagged_element_ids)} untagged geometries will be removed")

    # remove untagged elements from the final dictionary of geometries
    for untagged_element_id in untagged_element_ids:
        geometries.pop(untagged_element_id, None)

    # Create GeoDataFrame
    gdf = gpd.GeoDataFrame.from_dict(geometries, orient="index")

    # ensure gdf has a geometry col before assigning crs
    if "geometry" not in gdf.columns:
        # if there is no geometry column, create a null column
        gdf["geometry"] = np.nan
    gdf.set_geometry("geometry")

    # Set default crs
    gdf.crs = settings.default_crs

    # Apply .buffer(0) to any invalid geometries
    gdf = _buffer_invalid_geometries(gdf)

    # Filter final gdf to requested tags and bounding polygon
    gdf = _filter_gdf_by_polygon_and_tags(gdf, polygon=polygon, tags=tags)

    # bug in geopandas <=0.8.1 raises a TypeError if trying to plot empty geometries
    # but missing geometries (gdf['geometry'] = None) cannot be projected e.g. gdf.to_crs()
    # don't see any option other than to drop rows with missing/empty geometries
    gdf = gdf[~(gdf["geometry"].isna() | gdf["geometry"].is_empty)].copy()

    return gdf


def _parse_node_to_coord(element):
    """
    Parse coordinates from a node in the overpass response.

    The coords are only used to create LineStrings and Polygons.

    Parameters
    ----------
    element : dict
        element type "node" from overpass response JSON

    Returns
    -------
    coord : dict
        dict of lat and lon coordinates
    """
    # return the coordinate of a single node element
    coord = {"lat": element["lat"], "lon": element["lon"]}

    return coord


def _parse_node_to_point(element):
    """
    Parse point from a tagged node in the overpass response.

    The points are geometries in their own right.

    Parameters
    ----------
    element : dict
        element type "node" from overpass response JSON

    Returns
    -------
    point : dict
        dict of OSM ID, OSM element type, tags and geometry
    """
    point = {}
    point["osmid"] = element["id"]
    point["element_type"] = "node"

    if "tags" in element:
        for tag in element["tags"]:
            point[tag] = element["tags"][tag]

    geometry = Point(element["lon"], element["lat"])

    point["geometry"] = geometry

    return point


def _parse_way_to_linestring_or_polygon(element, coords, polygon_features):
    """
    Parse open LineString, closed LineString or Polygon from OSM 'way'.

    Please see https://wiki.openstreetmap.org/wiki/Overpass_turbo/Polygon_Features
    for more information on which tags should be parsed to polygons

    Parameters
    ----------
    element : dict
        element type "way" from overpass response JSON
    coords : dict
        dict of node IDs and their lat, lon coordinates
    polygon_features : dict
        dict for determining whether closed ways are LineStrings or Polygons

    Returns
    -------
    linestring_or_polygon : dict
        dict of OSM ID, OSM element type, nodes, tags and geometry
    """
    nodes = element["nodes"]

    linestring_or_polygon = {}
    linestring_or_polygon["osmid"] = element["id"]
    linestring_or_polygon["element_type"] = "way"
    linestring_or_polygon["nodes"] = nodes

    if "tags" in element:
        for tag in element["tags"]:
            linestring_or_polygon[tag] = element["tags"][tag]

    # if the OSM element is an open way (i.e. first and last nodes are not the same)
    # the geometry should be a Shapely LineString
    if element["nodes"][0] != element["nodes"][-1]:
        try:
            geometry = LineString([(coords[node]["lon"], coords[node]["lat"]) for node in nodes])
        except KeyError as e:
            # XMLs may include geometries that are incomplete, in which case return an empty geometry
            utils.log(
                f"node/{e} was not found in `coords`.\n"
                f"https://www.openstreetmap.org/{element['type']}/{element['id']} was not created."
            )
            geometry = LineString()

    # if the OSM element is a closed way (i.e. first and last nodes are the same)
    # depending upon the tags the geometry could be a Shapely LineString or Polygon
    elif element["nodes"][0] == element["nodes"][-1]:
        # Use the tags to determine whether the way represents a LineString, Polygon
        is_polygon = _closed_way_is_linestring_or_polygon(element, polygon_features)

        if is_polygon:
            try:
                geometry = Polygon([(coords[node]["lon"], coords[node]["lat"]) for node in nodes])
            except ValueError as e:
                # XMLs may include geometries that are incomplete, in which case return an empty geometry
                utils.log(
                    f"{e} . The geometry for "
                    f"https://www.openstreetmap.org/{element['type']}/{element['id']} was not created."
                )
                geometry = Polygon()
        else:
            try:
                geometry = LineString(
                    [(coords[node]["lon"], coords[node]["lat"]) for node in nodes]
                )
            except ValueError as e:
                # XMLs may include geometries that are incomplete, in which case return an empty geometry
                utils.log(
                    f"{e} . The geometry for "
                    f"https://www.openstreetmap.org/{element['type']}/{element['id']} was not created."
                )
                geometry = LineString()

    linestring_or_polygon["geometry"] = geometry

    return linestring_or_polygon


def _closed_way_is_linestring_or_polygon(element, polygon_features):
    """
    Determine whether a closed OSM way represents a LineString or Polygon.

    Closed OSM ways may represent LineStrings (e.g. a roundabout or hedge
    round a field) or Polygons (e.g. a building footprint or land use area)
    depending on the tags applied to them.

    The starting assumption is that it is not a polygon, however any polygon
    type tagging will return a polygon unless explicitly tagged with area:no.

    It is possible for a single closed OSM way to have both LineString and
    Polygon type tags (e.g. both barrier=fence and landuse=agricultural).
    OSMnx will return a single Polygon for elements tagged in this way.
    For more information see:
    https://wiki.openstreetmap.org/wiki/One_feature,_one_OSM_element)

    Parameters
    ----------
    element : dict
        closed element type "way" from overpass response JSON
    polygon_features : dict
        dict of tag keys with associated values and blocklist/passlist

    Returns
    -------
    is_polygon : boolean
        True if the tags are for a polygon type geometry
    """
    # the polygon_features dict is for determining which ways should become Polygons
    # therefore the starting assumption is that the geometry is a LineString
    is_polygon = False

    # get the element's tags
    element_tags = element.get("tags")

    # if the element doesn't have any tags leave it as a Linestring
    if element_tags is not None:

        # if the element is specifically tagged 'area':'no' -> LineString
        if element_tags.get("area") == "no":
            is_polygon = False

        # if the element has tags and is not tagged 'area':'no'
        # compare its tags with the polygon_features dictionary
        else:
            # identify common keys in the element's tags and the polygon_features dictionary
            intersecting_keys = element_tags.keys() & polygon_features.keys()

            # if common keys are found
            if len(intersecting_keys) > 0:

                # for each key in the intersecting keys
                for key in intersecting_keys:
                    # Get the key's value from the element's tags
                    key_value = element_tags.get(key)
                    # Determine if the key is for a blocklist or passlist in the polygon_features dictionary
                    blocklist_or_passlist = polygon_features.get(key).get("polygon")
                    # Get the values for the key from the polygon_features dictionary
                    polygon_features_values = polygon_features.get(key).get("values")

                    # if all features with that key should be polygons -> Polygon
                    if blocklist_or_passlist == "all":
                        is_polygon = True

                    # if the key is for a blocklist i.e. tags that should not become Polygons
                    elif blocklist_or_passlist == "blocklist":
                        # if the value for that key in the element is not in the blocklist -> Polygon
                        if key_value not in polygon_features_values:
                            is_polygon = True

                    # if the key is for a passlist i.e. specific tags should become Polygons
                    elif blocklist_or_passlist == "passlist":
                        # if the value for that key in the element is in the passlist -> Polygon
                        if key_value in polygon_features_values:
                            is_polygon = True

    return is_polygon


def _parse_relation_to_multipolygon(element, geometries):
    """
    Parse multipolygon from OSM relation (type:MultiPolygon).

    See more information about relations from OSM documentation:
    http://wiki.openstreetmap.org/wiki/Relation

    Parameters
    ----------
    element : dict
        element type "relation" from overpass response JSON
    geometries : dict
        dictionary containing all linestrings and polygons generated from OSM ways

    Returns
    -------
    multipolygon : dictionary
        dictionary of tags and geometry for a single multipolygon
    """
    multipolygon = {}
    multipolygon["osmid"] = element["id"]
    multipolygon["element_type"] = "relation"

    # Parse member 'way' ids
    member_way_refs = [member["ref"] for member in element["members"] if member["type"] == "way"]
    multipolygon["ways"] = member_way_refs

    # Add the tags
    if "tags" in element:
        for tag in element["tags"]:
            multipolygon[tag] = element["tags"][tag]

    # Extract the ways from the geometries dictionary using their unique id.
    # XMLs exported from the openstreetmap.org homepage with a bounding box
    # may include the relation but not the ways outside the bounding box.
    try:
        member_ways = [geometries[f"way/{member_way_ref}"] for member_way_ref in member_way_refs]
    except KeyError as e:
        utils.log(
            f"{e} was not found in `geometries`.\nThe geometry for "
            f"https://www.openstreetmap.org/{element['type']}/{element['id']} was not created."
        )
        multipolygon["geometry"] = MultiPolygon()
        return multipolygon

    # Extract the nodes of those ways
    member_nodes = [[member_way["nodes"] for member_way in member_ways]]
    multipolygon["nodes"] = member_nodes

    # Assemble MultiPolygon component polygons from component LineStrings and Polygons
    outer_polygons, inner_polygons = _assemble_multipolygon_component_polygons(element, geometries)

    # Subtract inner polygons from outer polygons
    geometry = _subtract_inner_polygons_from_outer_polygons(element, outer_polygons, inner_polygons)

    multipolygon["geometry"] = geometry

    return multipolygon


def _assemble_multipolygon_component_polygons(element, geometries):
    """
    Assemble a MultiPolygon from its component LineStrings and Polygons.

    The OSM wiki suggests an algorithm for assembling multipolygon geometries
    https://wiki.openstreetmap.org/wiki/Relation:multipolygon/Algorithm.
    This method takes a simpler approach relying on the accurate tagging
    of component ways with 'inner' and 'outer' roles as required on this page
    https://wiki.openstreetmap.org/wiki/Relation:multipolygon.

    Parameters
    ----------
    element : dict
        element type "relation" from overpass response JSON
    geometries : dict
        dict containing all linestrings and polygons generated from OSM ways

    Returns
    -------
    geometry : MultiPolygon
        a single Shapely MultiPolygon
    """
    outer_polygons = []
    inner_polygons = []
    outer_linestrings = []
    inner_linestrings = []

    # get the linestrings and polygons that make up the multipolygon
    for member in element["members"]:
        if member.get("type") == "way":
            # get the member's geometry from linestrings_and_polygons
            linestring_or_polygon = geometries.get(f"way/{member['ref']}")
            # sort it into one of the lists according to its role and geometry
            if (member.get("role") == "outer") and (
                linestring_or_polygon["geometry"].geom_type == "Polygon"
            ):
                outer_polygons.append(linestring_or_polygon["geometry"])
            elif (member.get("role") == "inner") and (
                linestring_or_polygon["geometry"].geom_type == "Polygon"
            ):
                inner_polygons.append(linestring_or_polygon["geometry"])
            elif (member.get("role") == "outer") and (
                linestring_or_polygon["geometry"].geom_type == "LineString"
            ):
                outer_linestrings.append(linestring_or_polygon["geometry"])
            elif (member.get("role") == "inner") and (
                linestring_or_polygon["geometry"].geom_type == "LineString"
            ):
                inner_linestrings.append(linestring_or_polygon["geometry"])

    # Merge outer linestring fragments. Returns a single LineString or MultiLineString collection
    merged_outer_linestrings = linemerge(outer_linestrings)

    # polygonize each linestring separately and append to the list of outer polygons
    if merged_outer_linestrings.geom_type == "LineString":
        outer_polygons += polygonize(merged_outer_linestrings)
    elif merged_outer_linestrings.geom_type == "MultiLineString":
        for merged_outer_linestring in list(merged_outer_linestrings):
            outer_polygons += polygonize(merged_outer_linestring)

    # Merge inner linestring fragments. Returns a single LineString or MultiLineString collection
    merged_inner_linestrings = linemerge(inner_linestrings)

    # polygonize each linestring separately and append to the list of inner polygons
    if merged_inner_linestrings.geom_type == "LineString":
        inner_polygons += polygonize(merged_inner_linestrings)
    elif merged_inner_linestrings.geom_type == "MultiLineString":
        for merged_inner_linestring in list(merged_inner_linestrings):
            inner_polygons += polygonize(merged_inner_linestring)

    if len(outer_polygons) == 0:
        utils.log(
            "No outer polygons were created for"
            f" https://www.openstreetmap.org/{element['type']}/{element['id']}"
        )

    return outer_polygons, inner_polygons


def _subtract_inner_polygons_from_outer_polygons(element, outer_polygons, inner_polygons):
    """
    Subtract inner polygons from outer polygons.

    Creates a Polygon or MultiPolygon with holes.

    Parameters
    ----------
    element : dict
        element type "relation" from overpass response JSON
    outer_polygons : list
        list of outer polygons that are part of a multipolygon
    inner_polygons : list
        list of inner polygons that are part of a multipolygon

    Returns
    -------
    geometry : Polygon or MultiPolygon
        a single Shapely Polygon or MultiPolygon
    """
    # create a new list to hold the outer polygons with the inner polygons subtracted
    outer_polygons_with_holes = []

    # loop through the outer polygons subtracting the inner polygons and appending to the list
    for outer_polygon in outer_polygons:
        for inner_polygon in inner_polygons:
            if inner_polygon.within(outer_polygon):
                try:
                    outer_polygon = outer_polygon.difference(inner_polygon)
                except TopologicalError as e:
                    print(
                        e,
                        f"\n MultiPolygon Relation OSM id {element['id']} difference failed,"
                        " trying with geometries buffered by 0.",
                    )
                    utils.log(
                        f"relation https://www.openstreetmap.org/relation/{element['id']} caused"
                        " a TopologicalError, trying with zero buffer."
                    )
                    outer_polygon = outer_polygon.buffer(0).difference(inner_polygon.buffer(0))

        # note: .buffer(0) can return either a Polygon or MultiPolygon
        # if it returns a MultiPolygon we need to extract the component
        # sub Polygons to add to outer_polygons_with_holes
        if outer_polygon.geom_type == "Polygon":
            outer_polygons_with_holes.append(outer_polygon)
        elif outer_polygon.geom_type == "MultiPolygon":
            outer_polygons_with_holes.extend(list(outer_polygon))

    # if only one polygon with holes was created, return that single polygon
    if len(outer_polygons_with_holes) == 1:
        geometry = outer_polygons_with_holes[0]
    # otherwise create a multipolygon from the list of outer polygons with holes
    else:
        geometry = MultiPolygon(outer_polygons_with_holes)

    return geometry


def _buffer_invalid_geometries(gdf):
    """
    Buffer any invalid geometries remaining in the GeoDataFrame.

    Invalid geometries in the GeoDataFrame (which may accurately
    reproduce invalid geometries in OpenStreetMap) can cause the
    filtering to the query polygon and other subsequent geometric
    operations to fail. This function logs the ids of the invalid
    geometries and applies a buffer of zero to try to make them valid.

    Note: the resulting geometries may differ from the originals
    - please check them against OpenStreetMap

    Parameters
    ----------
    gdf : GeoDataFrame
        the GeoDataFrame with potential invalid geometries

    Returns
    -------
    gdf : GeoDataFrame
        the GeoDataFrame with .buffer(0) applied to invalid geometries
    """
    # only apply the filters if the GeoDataFrame is not empty
    if not gdf.empty:

        # create a filter for rows with invalid geometries
        invalid_geometry_filter = ~gdf["geometry"].is_valid

        # if there are invalid geometries
        if sum(invalid_geometry_filter) > 0:

            # get their unique_ids from the index
            invalid_geometry_ids = gdf.loc[invalid_geometry_filter].index.to_list()

            # create a list of their urls and log them
            osm_url = "https://www.openstreetmap.org/"
            invalid_geom_urls = [osm_url + unique_id for unique_id in invalid_geometry_ids]
            utils.log(
                f"Invalid geometries that had .buffer(0) applied: {invalid_geom_urls}.",
                level=lg.WARNING,
            )

            # apply .buffer(0)
            gdf.loc[invalid_geometry_filter, "geometry"] = gdf.loc[
                invalid_geometry_filter, "geometry"
            ].buffer(0)

    return gdf


def _filter_gdf_by_polygon_and_tags(gdf, polygon, tags):
    """
    Filter the GeoDataFrame to the requested bounding polygon and tags.

    Filters the GeoDataFrame to the query polygon and tags. Removes
    columns of all NaNs (that held values only in rows removed by the filters).
    Resets the index of the GeoDataFrame, writing it into a new column called
    'unique_id'.

    Parameters
    ----------
    gdf : GeoDataFrame
        the GeoDataFrame to filter
    polygon : Polygon
        Shapely polygon defining the boundary of the requested area
    tags : dict
        the tags requested

    Returns
    -------
    gdf : GeoDataFrame
        final, filtered GeoDataFrame
    """
    # only apply the filters if the GeoDataFrame is not empty
    if not gdf.empty:

        # create two filters, initially all True
        polygon_filter = pd.Series(True, index=gdf.index)
        combined_tag_filter = pd.Series(True, index=gdf.index)

        # if a polygon was supplied, create a filter that is True for geometries
        # that intersect with the polygon
        if polygon:
            # get a set of index labels of the geometries that intersect the polygon
            gdf_indices_in_polygon = utils_geo._intersect_index_quadrats(gdf, polygon)
            # create a boolean series, True for geometries whose index is in the set
            polygon_filter = gdf.index.isin(gdf_indices_in_polygon)

        # if tags were supplied, create a filter that is True for geometries that have
        # at least one of the requested tags
        if tags:
            # Reset all values in the combined_tag_filter to False
            combined_tag_filter[:] = False

            # reduce the tags to those that are actually present in the GeoDataFrame columns
            tags_in_columns = {key: tags[key] for key in tags if key in gdf.columns}

            for key, value in tags_in_columns.items():
                if value is True:
                    tag_filter = gdf[key].notna()
                elif isinstance(value, str):
                    tag_filter = gdf[key] == value
                elif isinstance(value, list):
                    tag_filter = gdf[key].isin(value)

                combined_tag_filter = combined_tag_filter | tag_filter

        # apply the filters
        gdf = gdf[polygon_filter & combined_tag_filter].copy()

        # remove columns of all nulls (created by discarded component geometries)
        gdf.dropna(axis="columns", how="all", inplace=True)

        # reset the index keeping the unique ids
        gdf.reset_index(inplace=True)
        # rename the new 'index' column to 'unique_id'
        gdf.rename(columns={"index": "unique_id"}, inplace=True)

    return gdf