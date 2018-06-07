import psycopg2
from psycopg2.extras import RealDictCursor

from shapely.wkb import loads

from pyproj import Proj, transform

from requests import post
from requests.exceptions import ConnectionError

from .formatter import AddressFormatter


class Geocoder():

    def __init__(self, config):
        self.config = config
        self.db = self._init_db()
        self.formatter = AddressFormatter(self.config['opencage_data_file'])

    def _init_db(self):

        connstring = []
        for key, value in self.config['db'].items():
            connstring.append("{}={}".format(key, value))
        connection = psycopg2.connect(" ".join(connstring))

        return connection

    def forward(self, address, country=None, center=None):
        mercProj = Proj(init='epsg:3857')
        latlonProj = Proj(init='epsg:4326')

        # project center lat/lon to mercator
        merc_coordinate = None
        if center is not None:
            merc_coordinate = transform(latlonProj, mercProj, center[1], center[0])

        results = []
        for coordinate in self._fetch_coordinate(address, country=country, center=merc_coordinate):
            p = loads(coordinate['location'], hex=True)

            name = self.formatter.format(coordinate)
            lon, lat = transform(mercProj, latlonProj, p.x, p.y)
            results.append((
                name, lat, lon
            ))

        return results

    def reverse(self, lat, lon, limit=10):
        mercProj = Proj(init='epsg:3857')
        latlonProj = Proj(init='epsg:4326')

        # project center lat/lon to mercator
        merc_coordinate = transform(latlonProj, mercProj, lon, lat)

        for radius in [25, 50, 100]:
            item = next(self._fetch_address(merc_coordinate, radius, limit=limit))
            if item is not None:
                return self.formatter.format(item)

    def _fetch_address(self, center, radius, limit=10):
        query = '''
            SELECT house, road, house_number, postcode, city, min(distance) as distance FROM (
                SELECT
                b.name as house,
                b.road,
                b.house_number,
                pc.postcode,
                a.name as city,
                ST_Distance(b.geometry, ST_GeomFromText('POINT({x} {y})', 3857)) as distance
                FROM {buildings_table} b
                LEFT JOIN {postcode_table} pc
                ON ST_Contains(pc.geometry, ST_Centroid(b.geometry))
                LEFT JOIN {admin_table} a
                    ON (a.admin_level = 6 AND ST_Contains(a.geometry, ST_Centroid(b.geometry)))
                WHERE
                    ST_DWithin(
                        b.geometry,
                        ST_GeomFromText('POINT({x} {y})', 3857),
                        {radius}
                    )
                ORDER BY ST_Distance(b.geometry, ST_GeomFromText('POINT({x} {y})', 3857))
                LIMIT {limit}
            ) n
            GROUP BY house, road, house_number, postcode, city
            ORDER BY min(distance)
        '''.format(
            postcode_table=self.config['tables']['postcode'],
            buildings_table=self.config['tables']['buildings'],
            admin_table=self.config['tables']['admin'],
            x=center[0],
            y=center[1],
            radius=radius,
            limit=limit,
        )

        cursor = self.db.cursor(cursor_factory=RealDictCursor)
        cursor.execute(query)

        for result in cursor:
            yield result

    def _fetch_coordinate(self, search_term, center=None, country=None, limit=20):
        cursor = self.db.cursor(cursor_factory=RealDictCursor)

        try:
            response = post(self.config['postal_service_url'] + '/split', json={"query": search_term})
            if response.status_code == 200:
                parsed_address = response.json()[0]
            else:
                parsed_address = { 'road': search_term }
        except ConnectionError:
            parsed_address = { 'road': search_term }

        #
        # crude query builder following
        #

        # add a where clause for each resolved address field
        q = []
        v = []
        for query_part in ['road', 'house_number']:
            if query_part in parsed_address:
                q.append('b.{field} %% %s'.format(field=query_part))
                v.append(parsed_address[query_part])

        # create a trigram distance function for sorting
        if 'road' in parsed_address:
            # base it on the road from the address
            trgm_dist = 'road <-> %s as trgm_dist'
            v.insert(0, parsed_address['road'])
        else:
            # no basis for trigram distance
            trgm_dist = '0 as trgm_dist'

        where = " AND ".join(q)

        # distance sorting
        if center is None:
            distance = ''
            order_by_distance = ''
        else:
            distance = ", ST_Distance(ST_Centroid(b.geometry), ST_GeomFromText('POINT({x} {y})', 3857)) as dist".format(
                x=center[0],
                y=center[1]
            )
            order_by_distance = 'dist ASC,'

        if 'postcode' in parsed_address:
            # resolve post code area
            query = '''
                SELECT
                    b.*,
                    pc.postcode,
                    a.name as city,
                    {trgm_dist},
                    ST_Centroid(b.geometry) as location
                    {distance}
                FROM {postcode_table} pc
                JOIN {buildings_table} b
                    ON ST_Contains(pc.geometry, ST_Centroid(b.geometry))
                LEFT JOIN {admin_table} a
                    ON (a.admin_level = 6 AND ST_Contains(a.geometry, ST_Centroid(b.geometry)))
                WHERE
                    pc.postcode = %s
                    AND ({where})
                ORDER BY {order_by_distance} trgm_dist DESC
                LIMIT {limit};
            '''.format(
                trgm_dist=trgm_dist,
                distance=distance,
                postcode_table=self.config['tables']['postcode'],
                buildings_table=self.config['tables']['buildings'],
                admin_table=self.config['tables']['admin'],
                where=where,
                order_by_distance=order_by_distance,
                limit=limit,
            )
            if 'road' in parsed_address:
                v.insert(1, parsed_address['postcode'])
            else:
                v.insert(0, parsed_address['postcode'])
        elif 'city' in parsed_address:
            # run the query by the admin table
            query = '''
                SELECT
                    b.*,
                    a.name as city,
                    pc.postcode,
                    {trgm_dist},
                    ST_Centroid(b.geometry) as location
                    {distance}
                FROM {admin_table} a
                JOIN {buildings_table} b
                    ON ST_Contains(a.geometry, ST_Centroid(b.geometry))
                LEFT JOIN {postcode_table} pc
                    ON ST_Contains(pc.geometry, ST_Centroid(b.geometry))
                WHERE
                    a.name %% %s
                    AND a.admin_level = 6
                    AND ({where})
                ORDER BY {order_by_distance} trgm_dist DESC
                LIMIT {limit};
            '''.format(
                trgm_dist=trgm_dist,
                distance=distance,
                admin_table=self.config['tables']['admin'],
                buildings_table=self.config['tables']['buildings'],
                postcode_table=self.config['tables']['postcode'],
                where=where,
                order_by_distance=order_by_distance,
                limit=limit,
            )
            if 'road' in parsed_address:
                v.insert(1, parsed_address['city'])
            else:
                v.insert(0, parsed_address['city'])
        else:
            # search road name only
            query = '''
                SELECT
                    b.*,
                    pc.postcode,
                    a.name as city,
                    {trgm_dist},
                    ST_Centroid(b.geometry) as location
                    {distance}
                FROM {buildings_table} b
                LEFT JOIN {postcode_table} pc
                ON ST_Contains(pc.geometry, ST_Centroid(b.geometry))
                LEFT JOIN {admin_table} a
                    ON (a.admin_level = 6 AND ST_Contains(a.geometry, ST_Centroid(b.geometry)))
                WHERE
                    {where}
                ORDER BY {order_by_distance} trgm_dist DESC
                LIMIT {limit};
            '''.format(
                trgm_dist=trgm_dist,
                distance=distance,
                admin_table=self.config['tables']['admin'],
                buildings_table=self.config['tables']['buildings'],
                postcode_table=self.config['tables']['postcode'],
                where=where,
                order_by_distance=order_by_distance,
                limit=limit,
            )

        # run the geocoding query
        cursor.execute(query, v)

        for result in cursor:
            yield result
