from locale import normalize
import os
import re

import datetime

import json
import requests
import logging
logging.basicConfig(encoding='utf-8', level=logging.INFO)

import exifread
from exifread.utils import get_gps_coords
from PIL import Image
import unidecode

FLICKR_API_KEY = "The Flickr API key"
POS_STACK_API_KEY = "The Position Stack API key"

def _addExifUserComment(filename: str, comment: str) -> None:
    with Image.open(filename) as im:
        normalizedComment = unidecode.unidecode(comment)
        exif = im.getexif()
        # https://www.awaresystems.be/imaging/tiff/tifftags/privateifd/exif/usercomment.html
        if 0x9286 in exif:
            if str(exif[0x9286]).find(normalizedComment) < 0:
                exif[0x9286] = exif[0x9286] + ";" + normalizedComment
        else:
            exif[0x9286] = normalizedComment
        im.save(filename, exif=exif)

def _getGeoLocationInfo(latLong: tuple) -> dict:
    lat, long = latLong
    r = requests.get(f"http://api.positionstack.com/v1/reverse?access_key={POS_STACK_API_KEY}&query={lat},{long}&limit=1&output=json")
    data = json.loads(r.text)
    l = data["data"][0]
    res = {}
    res["city"] = l["name"]
    res["region"] = l["region"]
    res["country"] = l["country"]
    return res

def _getPhotoGeoLocationFromExif(fname: str) -> dict:
    with open(fname, 'rb') as f:
        tags = exifread.process_file(f, details=False)
        tuple = get_gps_coords(tags)
        if tuple:
            return _getGeoLocationInfo(tuple)
            
    return None

def _getPhotoDateTakenFromExif(fname: str) -> dict:
    with open(fname, 'rb') as f:
        tags = exifread.process_file(f, details=False)
        # 2022:03:19 15:49:42
        x = re.split(r":| ", str(tags["EXIF DateTimeOriginal"]))
        dt = datetime.datetime(int(x[0]), int(x[1]), int(x[2]))
        return dt

def _getPhotoGeoLocationFromFlickr(id: str) -> dict:
    r = requests.get(f"https://www.flickr.com/services/rest/?method=flickr.photos.geo.getLocation&api_key={FLICKR_API_KEY}&photo_id={id}&format=json&nojsoncallback=1")
    data = json.loads(r.text)
    l = data["photo"]["location"]
    res = {}
    res["city"] = l["locality"]["_content"] if "locality" in l else l["county"]["_content"]
    res["region"] = l["region"]["_content"]
    res["country"] = l["country"]["_content"]
    return res

def getAllPublicPhotosFromFlickr() -> dict:
    res = {}
    # count of pages is 6 -> count of photos / 500
    for i in range(1, 7):
        r = requests.get(f"https://www.flickr.com/services/rest/?method=flickr.people.getPublicPhotos&api_key={FLICKR_API_KEY}&user_id=138578208%40N06&per_page=500&page={i}&extras=date_taken%2Cgeo&format=json&nojsoncallback=1")
        data = json.loads(r.text)
        for photo in data["photos"]["photo"]:
            logging.debug(f"Loading Flickr photo {photo}")
            title = photo["title"]
            v = {}
            v["id"] = photo["id"]
            # 2021-10-03 13:25:48
            x = re.split(r"-|:| ", str(photo["datetaken"]))
            v["date"] = datetime.datetime(int(x[0]), int(x[1]), int(x[2]))
            v["latitude"] = photo["latitude"]
            v["longitude"] = photo["longitude"]
            if title in res:
                res[title].append(v)
            else:
                res[title] = [ v ]
    return res

def _findMatchingFlickrPhotoByDateTaken(filepath: str, flickPhotoList: list[dict]) -> dict:
    exifDate = _getPhotoDateTakenFromExif(filepath)
    if len(flickPhotoList) > 1:
        for p in flickPhotoList:
            diff = exifDate - p["date"]
            # +/- 1 day due to possible timezones differences
            if abs(diff.days) <= 1:
                return p
    else:
        diff = exifDate - flickPhotoList[0]["date"]
        # +/- 1 day due to possible timezones differences
        if abs(diff.days) <= 1:
            return flickPhotoList[0]
    return None

def _findFirstClosesMeaningfulParentDirName(filepath: str) -> str: 
    for t in reversed(os.path.dirname(filepath).split(os.path.sep)):
        # skip date
        if not re.search("^\d+-\d+-\d+$", t):
            return t

def _getImageGeoLocation(filepath: str, filenameWoExtension: str) -> dict:
    geo = None
    if filenameWoExtension in FLICKR_PHOTOS:
        flickPhotoList = FLICKR_PHOTOS[filenameWoExtension]
        logging.debug(f"Found {flickPhotoList}")
        try:
            flickrPhoto = _findMatchingFlickrPhotoByDateTaken(filepath, flickPhotoList)
            if not flickrPhoto:
                raise Exception("No Flickr photo matches by date taken")            
            geo = _getPhotoGeoLocationFromFlickr(flickrPhoto["id"])
            logging.debug(f"Using flickr photo {flickrPhoto['id']}")
        except Exception as e:
            logging.error(e)
            geo = None
            pass

    # try to use EXIF GPS information of the file     
    if not geo:
        try:
            return _getPhotoGeoLocationFromExif(filepath)
        except Exception as e:
            logging.error(e)
            geo = None
            pass

    return geo

def processImageFile(filepath: str):
    logging.info(f"Processing '{filepath}'")
    
    filename = os.path.basename(filepath)
    if not filename.lower().startswith("dsc"):
        logging.warning(f"Unsupported filename (must start with 'DSC') - skipping {filepath}")
        return
    if filename.find("__") > 0:
        logging.info(f"Skipping already processed {filepath}")
        return
        
    split = os.path.splitext(filename)
    extension = split[-1]
    filenameWoExtension = "".join(split[:-1])
    geo = _getImageGeoLocation(filepath, filenameWoExtension)
    location = ""
    if geo:
        if geo['country'].lower() == "bohemia":
            geo['country'] = "Czechia"
        
        location = f"{geo['city']}::{geo['region']}::{geo['country']}"
    else:
        logging.warning(f"No geo location acquired. Fallback to closest meaningful parent folder name.")
        location = _findFirstClosesMeaningfulParentDirName(filepath)
    
    _addExifUserComment(filepath, location)
    
    newFileName = f"{filenameWoExtension}__{location}{extension}"
    logging.info(f"Renaming to '{newFileName}'")
    os.rename(filepath, os.path.join(os.path.dirname(filepath), newFileName))

def browseImagesInDirectory(dirName: str, callback) -> None:
    logging.debug(f"browsing '{dirName}'")
    for filename in sorted(os.listdir(dirName)):
        path = os.path.join(dirName, filename)
        # checking if it is a file
        if os.path.isfile(path):
            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.gif')):
                callback(path)
        elif os.path.isdir(path):
            browseImagesInDirectory(os.path.relpath(path), callback)

###### MAIN ######

FLICKR_PHOTOS = getAllPublicPhotosFromFlickr()
logging.info(f"Loaded {len(FLICKR_PHOTOS)} Flickr photo titles (with possibly multiple photo IDs)")

browseImagesInDirectory("Photos", processImageFile)
