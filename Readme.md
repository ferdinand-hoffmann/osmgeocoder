# OSMGeocoder

Python implementation for a OSM Geocoder
(Only works on Python 3 for now)

## TODO

- Do not run geocoder on buildings only, probably roads and distances are possible

## Quick and dirty how-to

1. Create a PostgreSQL Database with PostGIS activated
2. Fetch a copy of [imposm3](https://github.com/omniscale/imposm3)
3. Get a OpenStreetMap data file (for example from [Geofabrik](http://download.geofabrik.de/), start with a small region!)
4. Import some OpenStreetMap data into the DB:
```bash
$ imposm import -connection postgis://user:password@host:port/database -mapping doc/imposm_mapping.yml -read /path/to/osm.pbf -write -deployproduction -optimize
```
5. Create the trigram search extension for the DB:
```sql
CREATE EXTENSION pg_trgm;
```
6. Create a trigram search indices (this could take a while):
```bash
psql osm < doc/create_trigram_indexes.sql
```
7. Create a virtualenv and install packages:
```bash
mkvirtualenv -p /usr/bin/python3 osmgeocoder
workon osmgeocoder
pip install -r requirements.txt
```
8. Modify configuration file to match your setup. The example config is in `config/config.json`.
9. Optionally install and start the postal machine learning address categorizer (see below)
10. Geocode:
```bash
python address2coordinate.py --config config/config.json --center 48.3849 10.8631 Lauterl
python coordinate2address.py --config config/config.json 48.3849 10.8631
```

## Optional support for libpostal

### Installation of libpostal

Be aware that the make process will download some data-files (about 1GB in size). The installation of libpostal
will need around 1 GB of disk space and about 2 GB of disk space while compiling.

Currently there is no Ubuntu package for `libpostal`, so we have to install it by hand:

```bash
git clone https://github.com/openvenues/libpostal
cd libpostal
./bootstrap.sh
./configure --prefix=/opt/libpostal --datadir=/opt/libpostal/share
make -j4
sudo make install
echo "/opt/libpostal/lib" | sudo tee /etc/ld.so.conf.d/libpostal.conf
sudo ldconfig
echo 'export PKG_CONFIG_PATH="$PKG_CONFIG_PATH:/opt/libpostal/lib/pkgconfig"' | sudo tee /etc/profile.d/libpostal.sh
```

Now log out and on again or run a new login shell (e.g. `bash -l`) and install the missing python modules:

```bash
workon osmgeocoder
CFLAGS="-L/opt/libpostal/lib -I/opt/libpostal/include" pip install postal
pip install gunicorn
pip install flask
```

### Run the classifier service

```bash
workon osmgeocoder
python postal_service.py --config config/config.json
```

Attention: Depending on the speed of your disk, startup of this service may take some seconds
(this is why this is implemented as a service) and it will take about 2 GB of RAM, so be warned!


If you want to run it in production mode just run it with `gunicorn` directly.
See the [Gunicorn documentation](http://docs.gunicorn.org/en/latest/settings.html) for further information.
Simple Example is following (one worker, run as daemon, bind to 127.0.0.1:3200):

```bash
workon osmgeocoder
gunicorn postal_service:app \
    --bind 127.0.0.1:3200 \
    --workers 1 \
    --pid /var/run/postal_service.pid
    --log-file /var/log/postal_service.log
    --daemon
```
