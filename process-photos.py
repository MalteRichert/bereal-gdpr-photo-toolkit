import json
import subprocess
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageOps, ExifTags
import logging
from pathlib import Path
import piexif
import os
import time
import shutil
from iptcinfo3 import IPTCInfo

# Try to import zoneinfo for proper timezone handling (Python 3.9+)
try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback for Python < 3.9
    try:
        from backports.zoneinfo import ZoneInfo
    except ImportError:
        ZoneInfo = None

# ANSI escape codes for text styling
STYLING = {
    "GREEN": "\033[92m",
    "RED": "\033[91m",
    "BLUE": "\033[94m",
    "BOLD": "\033[1m",
    "RESET": "\033[0m",
}

# Centralized accept/deny strings for menu logic
accept_input_string = 'y'
deny_input_string = 'n'

#Setup log styling
class ColorFormatter(logging.Formatter):
    def format(self, record):
        message = super().format(record)
        if record.levelno == logging.INFO and "Finished processing" not in record.msg:
            message = STYLING["GREEN"] + message + STYLING["RESET"]
        elif record.levelno == logging.ERROR:
            message = STYLING["RED"] + message + STYLING["RESET"]
        elif "Finished processing" in record.msg:  # Identify the summary message
            message = STYLING["BLUE"] + STYLING["BOLD"] + message + STYLING["RESET"]
        return message

# Setup basic logging
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Setup logging with styling
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()
handler = logger.handlers[0]  # Get the default handler installed by basicConfig
handler.setFormatter(ColorFormatter('%(asctime)s - %(levelname)s - %(message)s'))

# Initialize counters
processed_files_count = 0
converted_files_count = 0
combined_files_count = 0
skipped_files_count = 0

# Static IPTC tags
source_app = "BeReal app"
processing_tool = "github/bereal-gdpr-photo-toolkit"
#keywords = ["BeReal"]

# Define lists to hold the paths of images to be combined
primary_images = []
secondary_images = []

# Define paths using pathlib
data_folder = 'resources/data'
photo_folder = Path(data_folder + '/Photos/post/')
bereal_folder = Path(data_folder + '/Photos/bereal')
output_folder = Path('out/__processed')
output_folder_combined = Path('out/__combined')
output_folder.mkdir(parents=True, exist_ok=True)  # Create the output folder if it doesn't exist

# Print the paths
print(STYLING["BOLD"] + "\nThe following paths are set for the input and output files:" + STYLING["RESET"])
print(f"Photo folder: {photo_folder}")
if os.path.exists(bereal_folder):
    print(f"Older photo folder: {bereal_folder}")
print(f"Output folder for singular images: {output_folder}")
print(f"Output folder for combined images: {output_folder_combined}")
#print("\nDeduplication is active. No files will be overwritten or deleted.")
print("")

# Function to count number of input files
def count_files_in_folder(folder_path):
    folder = Path(folder_path)
    file_count = len(list(folder.glob('*.webp')))
    return file_count

number_of_files = count_files_in_folder(photo_folder)
print(f"Number of WebP-files in {photo_folder}: {number_of_files}")

if os.path.exists(bereal_folder):
    number_of_files = count_files_in_folder(bereal_folder)
    print(f"Number of (older) WebP-files in {bereal_folder}: {number_of_files}")

# Settings
## Initial choice for accessing advanced settings
print(STYLING["BOLD"] + "\nDo you want to access advanced settings or run with default settings?" + STYLING["RESET"])
print("Default settings are:\n"
"1. Copied images are converted from WebP to JPEG\n"
"2. Converted images' filenames do not contain the original filename\n"
"3. Combined images are created on top of converted, singular images")
advanced_settings = input("\nEnter " + STYLING["BOLD"] + f"'{accept_input_string}'" + STYLING["RESET"] + "for advanced settings or press any key to continue with default settings: ").strip().lower()

if advanced_settings != accept_input_string:
    print("Continuing with default settings.\n")

## Default responses
convert_to_jpeg = accept_input_string
keep_original_filename = deny_input_string
create_combined_images = accept_input_string

## Proceed with advanced settings if chosen
if advanced_settings == accept_input_string:
    # User choice for converting to JPEG
    convert_to_jpeg = None
    while convert_to_jpeg not in [accept_input_string, deny_input_string]:
        convert_to_jpeg = input(STYLING["BOLD"] + f"\n1. Do you want to convert images from WebP to JPEG? ({accept_input_string}/{deny_input_string}): " + STYLING["RESET"]).strip().lower()
        if convert_to_jpeg == deny_input_string:
            print("Your images will not be converted. No additional metadata will be added.")
        if convert_to_jpeg not in [accept_input_string, deny_input_string]:
            logging.error(f"Invalid input. Please enter '{accept_input_string}' or '{deny_input_string}'.")

    # User choice for keeping original filename
    print(STYLING["BOLD"] + "\n2. There are two options for how output files can be named" + STYLING["RESET"] + "\n"
    "Option 1: YYYY-MM-DDTHH-MM-SS_primary/secondary_original-filename.jpeg\n"
    "Option 2: YYYY-MM-DDTHH-MM-SS_primary/secondary.jpeg\n"
    "This will only influence the naming scheme of singular images.")
    keep_original_filename = None
    while keep_original_filename not in [accept_input_string, deny_input_string]:
        keep_original_filename = input(STYLING["BOLD"] + f"Do you want to keep the original filename in the renamed file? ({accept_input_string}/{deny_input_string}): " + STYLING["RESET"]).strip().lower()
        if keep_original_filename not in [accept_input_string, deny_input_string]:
            logging.error(f"Invalid input. Please enter '{accept_input_string}' or '{deny_input_string}'.")

    # User choice for creating combined images
    create_combined_images = None
    while create_combined_images not in [accept_input_string, deny_input_string]:
        create_combined_images = input(STYLING["BOLD"] + f"\n3. Do you want to create combined images like the original BeReal memories? ({accept_input_string}/{deny_input_string}): " + STYLING["RESET"]).strip().lower()
        if create_combined_images not in [accept_input_string, deny_input_string]:
            logging.error(f"Invalid input. Please enter '{accept_input_string}' or '{deny_input_string}'.")

if convert_to_jpeg == deny_input_string and create_combined_images == deny_input_string:
    print("You chose not to convert images nor do you want to output combined images.\n"
    "The script will therefore only copy images to a new folder and rename them according to your choice without adding metadata or creating new files.\n"
    "Script will continue to run in 5 seconds.")
    #time.sleep(10)

# Function to convert WEBP to JPEG
def convert_webp_to_jpg(image_path):
    if image_path.suffix.lower() == '.webp':
        jpg_path = image_path.with_suffix('.jpg')
        try:
            with Image.open(image_path) as img:
                img.convert('RGB').save(jpg_path, "JPEG", quality=80)
                logging.info(f"Converted {image_path} to JPEG.")
            return jpg_path, True
        except Exception as e:
            logging.error(f"Error converting {image_path} to JPEG: {e}")
            return None, False
    else:
        return image_path, False

# Helper function to convert UTC datetime to German local time (CET/CEST)
# Since EXIF doesn't support timezones, we adjust the time value manually
def _utc_to_german_time(dt_utc):
    """
    Convert UTC datetime to German local time (CET/CEST).
    Returns a naive datetime with the German local time value.
    CET (winter): UTC+1
    CEST (summer): UTC+2
    """
    if ZoneInfo is not None:
        # Use zoneinfo for accurate DST handling
        german_tz = ZoneInfo("Europe/Berlin")
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        dt_german = dt_utc.astimezone(german_tz)
        # Return naive datetime with German local time value
        return dt_german.replace(tzinfo=None)
    else:
        # Fallback: Manual calculation based on DST rules
        # DST in Germany: Last Sunday in March at 2:00 AM to last Sunday in October at 3:00 AM
        dt_naive = dt_utc.replace(tzinfo=None) if dt_utc.tzinfo else dt_utc
        year = dt_naive.year
        
        # Find last Sunday in March (DST starts at 2:00 AM UTC, which is 3:00 AM CET)
        march_last = datetime(year, 3, 31)
        while march_last.weekday() != 6:  # 6 = Sunday
            march_last -= timedelta(days=1)
        # DST starts at 2:00 AM UTC on last Sunday of March
        dst_start = march_last.replace(hour=2, minute=0, second=0, microsecond=0)
        
        # Find last Sunday in October (DST ends at 3:00 AM CEST, which is 1:00 AM UTC)
        october_last = datetime(year, 10, 31)
        while october_last.weekday() != 6:  # 6 = Sunday
            october_last -= timedelta(days=1)
        # DST ends at 1:00 AM UTC on last Sunday of October
        dst_end = october_last.replace(hour=1, minute=0, second=0, microsecond=0)
        
        # Determine if we're in DST (summer time)
        is_dst = dst_start <= dt_naive < dst_end
        
        # Add offset: +2 hours for summer (CEST), +1 hour for winter (CET)
        offset_hours = 2 if is_dst else 1
        dt_german = dt_naive + timedelta(hours=offset_hours)
        
        return dt_german

# Helper function to convert latitude and longitude to EXIF-friendly format
def _convert_to_degrees(value):
    """Convert decimal latitude / longitude to degrees, minutes, seconds (DMS)"""
    d = int(value)
    m = int((value - d) * 60)
    s = (value - d - m/60) * 3600.00

    # Convert to tuples of (numerator, denominator)
    d = (d, 1)
    m = (m, 1)
    s = (int(s * 100), 100)  # Assuming 2 decimal places for seconds for precision

    return (d, m, s)

# Function to update EXIF data
def update_exif(image_path, datetime_original, location=None, caption=None):
    try:
        exif_dict = piexif.load(image_path.as_posix())

        # Ensure the '0th' and 'Exif' directories are initialized
        if '0th' not in exif_dict:
            exif_dict['0th'] = {}
        if 'Exif' not in exif_dict:
            exif_dict['Exif'] = {}

        # For debugging: Load and log the updated EXIF data
        #logging.info(f"Original EXIF data for {image_path}: {exif_dict}")

        # Update datetime original - convert UTC to German local time
        # EXIF DateTimeOriginal doesn't support timezone, so we store German local time value
        if datetime_original.tzinfo is not None:
            # Convert to UTC if timezone-aware
            datetime_utc = datetime_original.astimezone(timezone.utc)
        else:
            # If naive, assume it's already UTC and make it timezone-aware
            datetime_utc = datetime_original.replace(tzinfo=timezone.utc)
        
        # Convert UTC to German local time (CET/CEST) for EXIF
        datetime_german = _utc_to_german_time(datetime_utc)
        datetime_str = datetime_german.strftime("%Y:%m:%d %H:%M:%S")
        exif_dict['Exif'][piexif.ExifIFD.DateTimeOriginal] = datetime_str
        logging.info(f"Found datetime (UTC): {datetime_utc.strftime('%Y:%m:%d %H:%M:%S')}, converted to German time: {datetime_str}")
        logging.info(f"Added capture date and time.")

        # Update GPS information if location is provided
        if location and 'latitude' in location and 'longitude' in location:
            logging.info(f"Found location: {location}")
            gps_ifd = {
                piexif.GPSIFD.GPSLatitudeRef: 'N' if location['latitude'] >= 0 else 'S',
                piexif.GPSIFD.GPSLatitude: _convert_to_degrees(abs(location['latitude'])),
                piexif.GPSIFD.GPSLongitudeRef: 'E' if location['longitude'] >= 0 else 'W',
                piexif.GPSIFD.GPSLongitude: _convert_to_degrees(abs(location['longitude'])),
            }
            exif_dict['GPS'] = gps_ifd
            logging.info(f"Added GPS location: {gps_ifd}")

        # Transfer caption as title in ImageDescription
        if caption:
            logging.info(f"Found caption: {caption}")
            #exif_dict[piexif.ImageIFD.ImageDescription] = caption.encode('utf-8')
            exif_dict['0th'][piexif.ImageIFD.ImageDescription] = caption.encode('utf-8')
            logging.info(f"Updated title with caption.")

        
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, image_path.as_posix())
        logging.info(f"Updated EXIF data for {image_path}.")

        # For debugging: Load and log the updated EXIF data
        #updated_exif_dict = piexif.load(image_path.as_posix())
        #logging.info(f"Updated EXIF data for {image_path}: {updated_exif_dict}")
        
    except Exception as e:
        logging.error(f"Failed to update EXIF data for {image_path}: {e}")

# Function to update IPTC information
def update_iptc(image_path, caption):
    try:
        # Load the IPTC data from the image
        info = IPTCInfo(image_path, force=True)  # Use force=True to create IPTC data if it doesn't exist
        
        # Check for errors (known issue with iptcinfo3 creating _markers attribute error)
        if not hasattr(info, '_markers'):
            info._markers = []
        
        # Update the "Caption-Abstract" field
        if caption:
            info['caption/abstract'] = caption
            logging.info(f"Caption added to converted image.")

        # Add static IPTC tags and keywords
        info['source'] = source_app
        info['originating program'] = processing_tool

        # Save the changes back to the image
        info.save_as(image_path)
        logging.info(f"Updated IPTC Caption-Abstract for {image_path}")
    except Exception as e:
        logging.error(f"Failed to update IPTC Caption-Abstract for {image_path}: {e}")


# Functions to update MP4 metadata using ffmpeg
def _format_iso6709_location(latitude, longitude):
    """Return ISO 6709 string like +37.785834-122.406417/ used by QuickTime."""
    lat_sign = '+' if latitude >= 0 else '-'
    lon_sign = '+' if longitude >= 0 else '-'
    # Use up to 6 decimal places which is common for GPS
    return f"{lat_sign}{abs(latitude):.6f}{lon_sign}{abs(longitude):.6f}/"


def update_mp4_metadata(input_path, output_path, datetime_original, location=None):
    try:
        # Ensure we use UTC time for MP4 metadata
        if datetime_original.tzinfo is not None:
            # Convert to UTC if timezone-aware
            datetime_utc = datetime_original.astimezone(timezone.utc)
        else:
            # If naive, assume it's already UTC
            datetime_utc = datetime_original
        
        ffmpeg_cmd = [
            'ffmpeg', '-y',
            '-i', str(input_path),
            '-c', 'copy',
            '-movflags', 'use_metadata_tags',
            '-metadata', f"creation_time={datetime_utc.strftime('%Y-%m-%dT%H:%M:%S')}Z",
        ]
        if location and 'latitude' in location and 'longitude' in location:
            iso6709 = _format_iso6709_location(location['latitude'], location['longitude'])
            ffmpeg_cmd += ['-metadata', f"location={iso6709}"]

        ffmpeg_cmd.append(str(output_path))

        result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            logging.error(f"Failed to update MP4 metadata for {input_path}: {result.stderr.decode(errors='ignore')}")
            return False
        logging.info(f"Updated MP4 metadata for {output_path}.")
        return True
    except FileNotFoundError:
        logging.error("ffmpeg not found. Please install ffmpeg to write video metadata.")
        return False
    except Exception as e:
        logging.error(f"Unexpected error updating MP4 metadata for {input_path}: {e}")
        return False

# Function to handle deduplication
def get_unique_filename(path):
    if not path.exists():
        return path
    else:
        prefix = path.stem
        suffix = path.suffix
        counter = 1
        while path.exists():
            path = path.with_name(f"{prefix}_{counter}{suffix}")
            counter += 1
        return path

def combine_images_with_resizing(primary_path, secondary_path):
    # Parameters for rounded corners, outline and position
    corner_radius = 60
    outline_size = 7
    position = (55, 55)

    # Load primary and secondary images
    primary_image = Image.open(primary_path)
    secondary_image = Image.open(secondary_path)

    # Resize the secondary image using LANCZOS resampling for better quality
    scaling_factor = 1/3.33333333  
    width, height = secondary_image.size
    new_width = int(width * scaling_factor)
    new_height = int(height * scaling_factor)
    resized_secondary_image = secondary_image.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # Ensure secondary image has an alpha channel for transparency
    if resized_secondary_image.mode != 'RGBA':
        resized_secondary_image = resized_secondary_image.convert('RGBA')

    # Create mask for rounded corners
    mask = Image.new('L', (new_width, new_height), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, new_width, new_height), corner_radius, fill=255)

    # Apply the rounded corners mask to the secondary image
    resized_secondary_image.putalpha(mask)

    # Create a new blank image with the size of the primary image
    combined_image = Image.new("RGB", primary_image.size)
    combined_image.paste(primary_image, (0, 0))    

    # Draw the black outline with rounded corners directly on the combined image
    outline_layer = Image.new('RGBA', combined_image.size, (0, 0, 0, 0))  # Transparent layer for drawing the outline
    draw = ImageDraw.Draw(outline_layer)
    outline_box = [position[0] - outline_size, position[1] - outline_size, position[0] + new_width + outline_size, position[1] + new_height + outline_size]
    draw.rounded_rectangle(outline_box, corner_radius + outline_size, fill=(0, 0, 0, 255))

    # Merge the outline layer with the combined image
    combined_image.paste(outline_layer, (0, 0), outline_layer)

    # Paste the secondary image onto the combined image using its alpha channel as the mask
    combined_image.paste(resized_secondary_image, position, resized_secondary_image)

    return combined_image

# Function to clean up backup files left behind by iptcinfo3
def remove_backup_files(directory):
    # List all files in the given directory
    for filename in os.listdir(directory):
        # Check if the filename ends with '~'
        if filename.endswith('~'):
            # Construct the full path to the file
            file_path = os.path.join(directory, filename)
            try:
                # Remove the file
                os.remove(file_path)
                print(f"Removed backup file: {file_path}")
            except Exception as e:
                print(f"Failed to remove backup file {file_path}: {e}")

# Load the JSON file
try:
    with open(data_folder + '/posts.json', encoding="utf8") as f:
        data = json.load(f)
except FileNotFoundError:
    logging.error("JSON file not found. Please check the path.")
    exit()

# Process files
for entry in data:
    try:
        # Extract only the filename from the path and then append it to the photo_folder path
        primary_filename = Path(entry['primary']['path']).name
        secondary_filename = Path(entry['secondary']['path']).name
        
        primary_path = photo_folder / primary_filename
        secondary_path = photo_folder / secondary_filename

        if not os.path.exists(primary_path):
            primary_path = bereal_folder / primary_filename
            secondary_path = bereal_folder / secondary_filename

        # Parse datetime as UTC-aware (the 'Z' suffix indicates UTC)
        taken_at_naive = datetime.strptime(entry['takenAt'], "%Y-%m-%dT%H:%M:%S.%fZ")
        taken_at = taken_at_naive.replace(tzinfo=timezone.utc)
        location = entry.get('location')  # This will be None if 'location' is not present
        caption = entry.get('caption')  # This will be None if 'caption' is not present

        
        for path, role in [(primary_path, 'primary'), (secondary_path, 'secondary')]:
            logging.info(f"Found image: {path}")
            # Check if conversion to JPEG is enabled by the user
            if convert_to_jpeg == accept_input_string:
                # Convert WebP to JPEG if necessary
                converted_path, converted = convert_webp_to_jpg(path)
                if converted_path is None:
                    skipped_files_count += 1
                    continue  # Skip this file if conversion failed
                if converted:
                    converted_files_count += 1

            # Adjust filename based on user's choice
            time_str = taken_at.strftime("%Y-%m-%dT%H-%M-%S")  # ISO standard format with '-' instead of ':' for time
            original_filename_without_extension = Path(path).stem  # Extract original filename without extension
            
            if convert_to_jpeg == accept_input_string:
                if keep_original_filename == accept_input_string:
                    new_filename = f"{time_str}_{role}_{converted_path.name}"
                else:
                    new_filename = f"{time_str}_{role}.jpg"
            else:
                if keep_original_filename == accept_input_string:
                    new_filename = f"{time_str}_{role}_{original_filename_without_extension}.webp"
                else:
                    new_filename = f"{time_str}_{role}.webp"
            
            new_path = output_folder / new_filename
            new_path = get_unique_filename(new_path)  # Ensure the filename is unique
            
            if convert_to_jpeg == accept_input_string and converted:
                converted_path.rename(new_path)  # Move and rename the file
            else:
                shutil.copy2(path, new_path) # Copy to new path

            # Update EXIF and IPTC data for all images (converted or not)
            update_exif(new_path, taken_at, location, caption)                
            logging.info(f"EXIF data added to {role} image.")

            image_path_str = str(new_path)
            update_iptc(image_path_str, caption)

            if role == 'primary':
                primary_images.append({
                    'path': new_path,
                    'taken_at': taken_at,
                    'location': location,
                    'caption': caption
                })
            else:
                secondary_images.append(new_path)

            logging.info(f"Sucessfully processed {role} image.")
            processed_files_count += 1
            print("")

        # Process BTS (behind-the-scenes) video if present
        bts_media = entry.get('btsMedia')
        if bts_media and isinstance(bts_media, dict) and bts_media.get('path'):
            bts_filename = Path(bts_media['path']).name
            bts_path = Path(data_folder) / bts_filename

            if os.path.exists(bts_path) and bts_path.suffix.lower() == '.mp4':
                time_str = taken_at.strftime("%Y-%m-%dT%H-%M-%S")
                original_bts_stem = Path(bts_path).stem

                if keep_original_filename == accept_input_string:
                    bts_new_filename = f"{time_str}_bts_{original_bts_stem}.mp4"
                else:
                    bts_new_filename = f"{time_str}_bts.mp4"

                bts_new_path = output_folder / bts_new_filename
                bts_new_path = get_unique_filename(bts_new_path)

                # Write metadata into MP4 while copying
                temp_output = bts_new_path.with_suffix('.tmp.mp4')
                success = update_mp4_metadata(bts_path, temp_output, taken_at, location)
                if success:
                    temp_output.replace(bts_new_path)
                    logging.info(f"Successfully processed BTS video: {bts_new_path}")
                    processed_files_count += 1
                else:
                    # Fallback: just copy without metadata
                    shutil.copy2(bts_path, bts_new_path)
                    logging.error(f"Copied BTS video without metadata: {bts_new_path}")
                    processed_files_count += 1
    except Exception as e:
        logging.error(f"Error processing entry {entry}: {e}")

# Create combined images if user chose accept
if create_combined_images == accept_input_string:
    #Create output folder if it doesn't exist
    output_folder_combined.mkdir(parents=True, exist_ok=True)

    for primary_path, secondary_path in zip(primary_images, secondary_images):
        # Extract metadata from one of the images for consistency
        #taken_at = datetime.strptime(timestamp, "%Y-%m-%dT%H-%M-%S")
        primary_new_path = primary_path['path']
        primary_taken_at = primary_path['taken_at']
        primary_location = primary_path['location']
        primary_caption = primary_path['caption']

        timestamp = primary_new_path.stem.split('_')[0]

        # Construct the new file name for the combined image
        combined_filename = f"{timestamp}_combined.webp"
        combined_image = combine_images_with_resizing(primary_new_path, secondary_path)
        
        combined_image_path = output_folder_combined / (combined_filename)
        combined_image.save(combined_image_path, 'JPEG')
        combined_files_count += 1

        logging.info(f"Combined image saved: {combined_image_path}")

        update_exif(combined_image_path, primary_taken_at, primary_location, primary_caption)
        logging.info(f"Metadata added to combined image.")

        image_path_str = str(combined_image_path)
        update_iptc(image_path_str, primary_caption)

        if convert_to_jpeg == accept_input_string:
            # Convert WebP to JPEG if necessary
            converted_path, converted = convert_webp_to_jpg(combined_image_path)
            update_exif(converted_path, primary_taken_at, primary_location, primary_caption)
            logging.info(f"Metadata added to converted image.")
            image_path_str = str(converted_path)
            update_iptc(image_path_str, primary_caption)

            if converted_path is None:
                logging.error(f"Failed to convert combined image to JPEG: {combined_image_path}")
        print("")

# Clean up backup files
print(STYLING['BOLD'] + "Removing backup files left behind by iptcinfo3" + STYLING["RESET"])
remove_backup_files(output_folder)
if create_combined_images == accept_input_string: remove_backup_files(output_folder_combined)
print("")

# Summary
logging.info(f"Finished processing.\nNumber of input-files: {number_of_files}\nTotal files processed: {processed_files_count}\nFiles converted: {converted_files_count}\nFiles skipped: {skipped_files_count}\nFiles combined: {combined_files_count}")