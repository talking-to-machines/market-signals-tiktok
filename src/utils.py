import pandas as pd
import os
import ast
import yt_dlp
import time
import json
from pydub import AudioSegment
from apify_client import ApifyClient
from openai import OpenAI
from src.prompt_template import (
    finfluencer_identification_system_prompt,
    interview_system_prompt,
    video_transcript_template,
    interview_user_prompt,
)
from config.config import (
    PROJECT,
    PROFILESEARCH_VIDEO_METADATA_FILE,
    KEYWORDSEARCH_VIDEO_METADATA_FILE,
    PROFILESEARCH_PROFILE_METADATA_FILE,
    KEYWORDSEARCH_PROFILE_METADATA_FILE,
)

openai_client = OpenAI()
base_dir = os.path.dirname(os.path.abspath(__file__))


def load_text_file(file_path) -> list:
    """
    Load search terms for market signals or profile list from text file.

    Args:
        file_path (str): The path to the text file containing search terms/profiles, one per line.

    Returns:
        list: A list of search terms/profiles as strings.
    """
    full_file_path = f"{base_dir}/../config/{file_path}"
    with open(full_file_path, "r") as file:
        return [line.strip() for line in file]


def update_video_metadata(
    client: ApifyClient,
    run: dict,
    profile_search: bool,
    filtering_list: list,
) -> None:
    """
    Updates the video metadata by fetching new data, appending it to the existing data,
    and removing duplicates.

    Args:
        client (ApifyClient): The Apify client used to fetch video metadata.
        run (dict): The run object containing the default dataset ID.
        profile_search (bool): A boolean indicating whether the search was for profiles or not.
        filtering_list (list): A list of search terms or profiles used to filter the search results.
    """
    # Fetch extracted video metadata
    video_metadata = pd.DataFrame(
        list(client.dataset(run["defaultDatasetId"]).iterate_items())
    )

    # Filter out videos based on search terms or profiles to remove irrelevant entries
    if profile_search:
        video_metadata.rename(columns={"input": "profile"}, inplace=True)
        video_metadata = video_metadata[
            video_metadata["profile"].isin(filtering_list)
        ].reset_index(drop=True)
    else:  # keyword search
        video_metadata = video_metadata[
            video_metadata["searchQuery"].isin(filtering_list)
        ].reset_index(drop=True)

    # Append extraction time to extracted video metadata
    video_metadata["extractionTime"] = pd.Timestamp.utcnow()

    # Define the file path
    if profile_search:
        video_metadata_path = (
            f"{base_dir}/../data/{PROJECT}/{PROFILESEARCH_VIDEO_METADATA_FILE}"
        )
    else:
        video_metadata_path = (
            f"{base_dir}/../data/{PROJECT}/{KEYWORDSEARCH_VIDEO_METADATA_FILE}"
        )

    if os.path.exists(video_metadata_path):
        # Load existing video metadata file
        old_video_metadata = pd.read_csv(video_metadata_path)
        old_video_metadata["id"] = old_video_metadata["id"].astype("str")

        # Append new data
        video_metadata = pd.concat([old_video_metadata, video_metadata])

    # Remove duplicated video entries based on video ID, keeping the latest entry
    video_metadata.drop_duplicates(
        subset="id",
        keep="last",
        inplace=True,
    )

    # Save updated video metadata
    video_metadata.to_csv(video_metadata_path, index=False)

    return None


def convert_str_to_dictionary(str_to_convert: str) -> dict:
    """
    Converts a string representation of a dictionary to an actual dictionary.

    Args:
        str_to_convert (str): The string to convert to a dictionary.

    Returns:
        dict: The converted dictionary. If conversion fails, returns a dictionary with a single key 'id' set to None.
    """
    try:
        return ast.literal_eval(str_to_convert)
    except Exception as e:
        return {"id": None}


def update_profile_metadata(profile_search: bool) -> None:
    """
    Updates the profile metadata for a given project by processing the video metadata.

    Args:
        profile_search (bool): A boolean indicating whether the search was for profiles or not.
    """
    # Load video metadata file
    if profile_search:
        video_metadata_path = (
            f"{base_dir}/../data/{PROJECT}/{PROFILESEARCH_VIDEO_METADATA_FILE}"
        )
    else:
        video_metadata_path = (
            f"{base_dir}/../data/{PROJECT}/{KEYWORDSEARCH_VIDEO_METADATA_FILE}"
        )
    video_metadata = pd.read_csv(video_metadata_path)

    # Extract the authorMeta field
    profile_metadata = video_metadata[["authorMeta", "extractionTime"]]

    # Convert the authorMeta dictionary to separate columns
    profile_metadata.loc[:, "authorMeta"] = profile_metadata["authorMeta"].apply(
        convert_str_to_dictionary
    )
    profile_metadata = pd.json_normalize(profile_metadata["authorMeta"]).join(
        profile_metadata["extractionTime"]
    )
    profile_metadata.rename(columns={"name": "profile"}, inplace=True)
    profile_metadata["id"] = profile_metadata["id"].astype("str")

    # Remove duplicates based on profile ID, keeping the latest entry
    profile_metadata.drop_duplicates(
        subset="id",
        keep="last",
        inplace=True,
    )

    # Drop invalid profiles
    profile_metadata = profile_metadata[
        (~profile_metadata["id"].isin(["nan", "None"]))
        & (~profile_metadata["id"].isnull())
    ].reset_index(drop=True)

    # Save profile metadata locally, overwrite existing profile metadata if it exist
    if profile_search:
        profile_metadata_path = (
            f"{base_dir}/../data/{PROJECT}/{PROFILESEARCH_PROFILE_METADATA_FILE}"
        )
    else:
        profile_metadata_path = (
            f"{base_dir}/../data/{PROJECT}/{KEYWORDSEARCH_PROFILE_METADATA_FILE}"
        )
    profile_metadata.to_csv(profile_metadata_path, index=False)

    return None


def identify_top_influencers(top_n_profiles: int) -> None:
    """
    Identifies the top N influencers based on the number of followers from a profile metadata file
    and saves their profiles to a text file.

    Args:
        top_n_profiles (int): The number of top profiles to identify based on the number of followers.

    Returns:
        None
    """
    # Load profile metadata file based on keyword search
    profile_metadata_path = (
        f"{base_dir}/../data/{PROJECT}/{KEYWORDSEARCH_PROFILE_METADATA_FILE}"
    )
    profile_metadata = pd.read_csv(profile_metadata_path)

    # Sort profiles based on number of followers
    profile_metadata_sorted = profile_metadata.sort_values(
        by="fans", ascending=False
    ).reset_index(drop=True)

    # Identify top n profiles based on number of followers
    profile_metadata_top_n_profiles = profile_metadata_sorted.head(top_n_profiles)

    # Save top n profiles to a text file
    profiles = profile_metadata_top_n_profiles["profile"].tolist()
    profiles_path = f"{base_dir}/../config/{PROJECT}_profiles.txt"

    with open(profiles_path, "w") as file:
        for profile in profiles:
            file.write(f"{profile}\n")

    return None


def download_video(row: pd.Series, PROJECT: str) -> None:
    """
    Downloads a TikTok video using the provided information in the row.

    Args:
        row (pd.Series): A pandas Series containing the video information, including the 'webVideoUrl' and 'video_filename'.
        PROJECT (str): The project name used to construct the output file path.

    Returns:
        None
    """
    # The TikTok video link
    video_url = row["webVideoUrl"]

    # Output file name
    output_file = (
        f"{base_dir}/../data/{PROJECT}/video-downloads/{row['video_filename']}"
    )

    # Options for yt-dlp
    ydl_opts = {
        "outtmpl": output_file,  # Save the video with this file name
        "format": "best",  # Download the best quality available
    }

    # Download the video
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
    except Exception as e:
        print(f"An error occurred downloading {video_url}:", str(e))


def optimize_audio_file(input_file_path: str, output_file_path: str) -> None:
    """
    Optimize an audio file by downsampling it to 16 kHz and converting it to mono.

    Args:
        input_file_path (str): The path to the input audio file.
        output_file_path (str): The path where the optimized audio file will be saved.

    Returns:
        None
    """
    # Load the audio file
    audio = AudioSegment.from_file(input_file_path)

    # Downsample the audio to 16 kHz and convert to mono
    audio = audio.set_frame_rate(16000).set_channels(1)

    # Export the optimized audio file
    audio.export(output_file_path, format="wav")


def transcribe_videos(row: pd.Series, PROJECT: str) -> str:
    """
    Transcribes the audio from a video file using the OpenAI Whisper model.
    Args:
        row (pd.Series): A pandas Series containing information about the video file.
                         It must include a 'video_filename' key with the name of the video file.
        PROJECT (str): The name of the project, used to construct the file paths.
    Returns:
        str: The transcription of the audio if successful, otherwise None.
    Raises:
        FileNotFoundError: If the input video file is not found.
        Exception: For other errors encountered during transcription, including file size issues.
    """
    input_file_path = (
        f"{base_dir}/../data/{PROJECT}/video-downloads/{row['video_filename']}"
    )
    optimized_file_path = f"{base_dir}/../data/{PROJECT}/video-downloads/optimized_{row['video_filename'][:-4] + '.wav'}"

    try:
        with open(input_file_path, "rb") as audio_file:
            transcription = openai_client.audio.transcriptions.create(
                model="whisper-1", file=audio_file, response_format="text"
            )
        return transcription

    except FileNotFoundError:
        return None

    except Exception as e:
        if e.status_code == 413:
            print(
                f"Error: File {row['video_filename']} is too large to process. Optimizing the audio file..."
            )
            # Optimize the audio file
            optimize_audio_file(input_file_path, optimized_file_path)
            try:
                with open(optimized_file_path, "rb") as audio_file:
                    transcription = openai_client.audio.transcriptions.create(
                        model="whisper-1", file=audio_file, response_format="text"
                    )
                return transcription
            except Exception as e:
                print(
                    f"Error: File {optimized_file_path} is still too large after optimisation: {e}"
                )
                return None
        else:
            print(f"Error encountered when transcribing {row['video_filename']}: {e}")
            return None


def calculate_profile_engagement(num_likes: str, num_fans_videos: str) -> float:
    """
    Calculate the profile engagement based on the number of likes and the number of fans/videos posted.

    Args:
        num_likes (str): The number of likes as a string.
        num_fans_videos (str): The number of fans/videos posted.

    Returns:
        float: The profile engagement ratio. If the number of fans/videos posted is zero or cannot be converted to a number, returns 0.0.
    """
    num_likes = pd.to_numeric(num_likes, errors="coerce")
    num_fans_videos = pd.to_numeric(num_fans_videos, errors="coerce")

    # Replace NaN values with 0
    num_likes = num_likes if pd.notna(num_likes) else 0
    num_fans_videos = num_fans_videos if pd.notna(num_fans_videos) else 0

    profile_engagement = num_likes / num_fans_videos if num_fans_videos > 0 else 0.0
    return profile_engagement


def construct_system_prompt(row: pd.Series, is_interview: bool) -> str:
    if is_interview:
        system_prompt_template = interview_system_prompt
    else:
        system_prompt_template = finfluencer_identification_system_prompt

    system_prompt = system_prompt_template.format(
        profile_image=row["avatar"],
        profile_name=row["profile"],
        profile_nickname=row["nickName"],
        verified_status=row["verified"],
        private_account=row["privateAccount"],
        region=row["region"],
        tiktok_seller=row["ttSeller"],
        profile_signature=row["signature"],
        num_followers=row["fans"],
        num_following=row["following"],
        num_likes=row["heart"],
        num_videos=row["video"],
        num_digg=row["digg"],
        total_likes_over_num_followers=calculate_profile_engagement(
            row["heart"], row["fans"]
        ),
        total_likes_over_num_videos=calculate_profile_engagement(
            row["heart"], row["video"]
        ),
        video_transcripts=row["transcripts_combined"],
    )
    return system_prompt


def construct_interview_user_prompt() -> str:
    """
    Constructs a user prompt for an interview by loading Russell 4000 stock tickers from a CSV file,
    formatting them into a string, and inserting them into a predefined prompt template.

    Returns:
        str: The constructed user prompt containing the formatted Russell 4000 stock tickers.
    """
    # Load Russell 4000 stock tickers
    full_file_path = f"{base_dir}/../config/russell4000_stock_tickers.csv"
    russell4000_stock_tickers = pd.read_csv(full_file_path)

    # Construct Russell 4000 stock ticker string
    russell4000_stock_tickers["combined_ticker"] = russell4000_stock_tickers.apply(
        lambda row: f"{row['COMNAM']} ({row['TICKER']})", axis=1
    )
    russell4000_stock_ticker_list = russell4000_stock_tickers[
        "combined_ticker"
    ].to_list()
    russell4000_stock_ticker_str = ", ".join(russell4000_stock_ticker_list)

    # Construct user prompt
    user_prompt = interview_user_prompt.format(
        russell_4000_tickers=russell4000_stock_ticker_str
    )

    return user_prompt


def create_batch_file(
    prompts: pd.DataFrame,
    system_prompt_field: str,
    user_prompt_field: str = "question_prompt",
    batch_file_name: str = "batch_input.jsonl",
) -> str:
    """
    Creates a batch file in JSON Lines format from a DataFrame of prompts.

    Args:
        prompts (pd.DataFrame): DataFrame containing the prompts data.
        system_prompt_field (str): The column name in the DataFrame for the system prompt content.
        user_prompt_field (str, optional): The column name in the DataFrame for the user prompt content. Defaults to "question_prompt".
        batch_file_name (str, optional): The name of the output batch file. Defaults to "batch_input.jsonl".

    Returns:
        str: The name of the created batch file.
    """
    # Creating an array of json tasks
    tasks = []
    for i in range(len(prompts)):
        task = {
            "custom_id": f'{prompts.loc[i, "custom_id"]}',
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": "gpt-4o",  # gpt-4o or gpt-4-turbo
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": prompts.loc[i, system_prompt_field]},
                    {"role": "user", "content": prompts.loc[i, user_prompt_field]},
                ],
            },
        }
        tasks.append(task)

    # Creating batch file
    with open(
        f"{base_dir}/../data/{PROJECT}/batch-files/{batch_file_name}", "w"
    ) as file:
        for obj in tasks:
            file.write(json.dumps(obj) + "\n")

    return batch_file_name


def batch_query(
    batch_input_file_dir: str,
    batch_output_file_dir: str,
) -> pd.DataFrame:
    """
    Executes a batch query using the OpenAI API and processes the results into a pandas DataFrame.

    Args:
        batch_input_file_dir (str): The directory path of the batch input file.
        batch_output_file_dir (str): The directory path where the batch output file will be saved.

    Returns:
        pd.DataFrame: A DataFrame containing the processed results from the batch query.
    """
    # Load OpenAI client
    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
    )

    # Upload batch input file
    batch_file = client.files.create(
        file=open(
            f"{base_dir}/../data/{PROJECT}/batch-files/{batch_input_file_dir}", "rb"
        ),
        purpose="batch",
    )

    # Create batch job
    batch_job = client.batches.create(
        input_file_id=batch_file.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )

    # Check batch status
    while True:
        batch_job = client.batches.retrieve(batch_job.id)
        print(f"Batch job status: {batch_job.status}")
        if batch_job.status == "completed":
            break
        elif batch_job.status == "failed":
            raise Exception("Batch job failed.")
        else:
            # Wait for 5 minutes before checking again
            time.sleep(300)

    # Retrieve batch results
    result_file_id = batch_job.output_file_id
    results = client.files.content(result_file_id).content

    # Save the batch output
    with open(
        f"{base_dir}/../data/{PROJECT}/batch-files/{batch_output_file_dir}", "wb"
    ) as file:
        file.write(results)

    # Loading data from saved output file
    response_list = []
    with open(
        f"{base_dir}/../data/{PROJECT}/batch-files/{batch_output_file_dir}", "r"
    ) as file:
        for line in file:
            # Parsing the JSON result string into a dict
            result = json.loads(line.strip())
            response_list.append(
                {
                    "custom_id": f'{result["custom_id"]}',
                    "query_response": result["response"]["body"]["choices"][0][
                        "message"
                    ]["content"],
                }
            )

    return pd.DataFrame(response_list)


def extract_profile_id(author_metadata: str) -> str:
    """
    Extracts the profile ID from the given author metadata string.

    Args:
        author_metadata (str): A string representation of a dictionary containing author metadata.

    Returns:
        str: The profile ID extracted from the author metadata.
    """
    author_metadata_dict = ast.literal_eval(author_metadata)
    return str(author_metadata_dict.get("id"))


def extract_mentions(mentions_raw: str) -> str:
    """Extracts nicknames from a raw mentions string.
    This function takes a string representation of a list of dictionaries,
    where each dictionary contains a "nickName" key. It extracts the values
    associated with the "nickName" key and returns them as a comma-separated
    string.
    Args:
        mentions_raw (str): A string representation of a list of dictionaries,
                            where each dictionary contains a "nickName" key.
    Returns:
        str: A comma-separated string of nicknames. If an error occurs during
             processing, an empty string is returned.
    """
    try:
        nickname_list = []
        mentions_list = ast.literal_eval(mentions_raw)
        for mention in mentions_list:
            nickname_list.append(mention.get("nickName", ""))

        return ", ".join([nickname for nickname in nickname_list if nickname != ""])

    except Exception as e:
        return ""


def extract_hashtags(hashtags_raw: str) -> str:
    """
    Extracts hashtags from a raw string representation of a list of dictionaries.
    Args:
        hashtags_raw (str): A string representation of a list of dictionaries,
                            where each dictionary contains a "name" key.
    Returns:
        str: A comma-separated string of hashtag names. If an error occurs,
             an empty string is returned.
    """
    try:
        hashtag_name_list = []
        hashtags_list = ast.literal_eval(hashtags_raw)
        for hashtag in hashtags_list:
            hashtag_name_list.append(hashtag.get("name", ""))

        return ", ".join(
            [hashtag_name for hashtag_name in hashtag_name_list if hashtag_name != ""]
        )

    except Exception as e:
        return ""


def calculate_video_engagement(video_data: pd.Series) -> float:
    """
    Calculate the engagement rate of a video based on its interaction metrics.

    The engagement rate is calculated as the sum of likes, shares, comments, and saves
    divided by the number of views. If the number of views is zero, the engagement rate
    is set to 0.0 to avoid division by zero.

    Args:
        video_data (pd.Series): A pandas Series containing the video's interaction metrics.
            Expected keys are:
            - "diggCount": Number of likes.
            - "shareCount": Number of shares.
            - "commentCount": Number of comments.
            - "collectCount": Number of saves.
            - "playCount": Number of views.

    Returns:
        float: The engagement rate of the video.
    """
    num_likes = pd.to_numeric(video_data["diggCount"], errors="coerce")
    num_shares = pd.to_numeric(video_data["shareCount"], errors="coerce")
    num_comments = pd.to_numeric(video_data["commentCount"], errors="coerce")
    num_saves = pd.to_numeric(video_data["collectCount"], errors="coerce")
    num_views = pd.to_numeric(video_data["playCount"], errors="coerce")

    # Replace NaN values with 0
    num_likes = num_likes if pd.notna(num_likes) else 0
    num_shares = num_shares if pd.notna(num_shares) else 0
    num_comments = num_comments if pd.notna(num_comments) else 0
    num_saves = num_saves if pd.notna(num_saves) else 0
    num_views = num_views if pd.notna(num_views) else 0

    video_engagement = (
        (num_likes + num_shares + num_comments + num_saves) / num_views
        if num_views > 0
        else 0.0
    )
    return video_engagement


def extract_video_transcripts(profile_id, video_metadata) -> str:
    """
    Extracts and combines video transcripts for a given profile ID from the provided video metadata.

    Args:
        profile_id (str): The profile ID to filter the video metadata.
        video_metadata (pd.DataFrame): A DataFrame containing video metadata, including 'profile_id', 'createTimeISO', and 'video_transcript' columns.

    Returns:
        str: A single string containing the combined video transcripts, sorted by creation time from latest to oldest, with each transcript prefixed by its creation time.
    """
    # Filter the rows where profile_id matches
    filtered_videos = video_metadata[video_metadata["profile_id"] == profile_id].copy()

    # Sort the filtered videos by creation time from latest to oldest
    filtered_videos = filtered_videos.sort_values(
        by="createTimeISO", ascending=False
    ).reset_index(drop=True)

    # Join the list of video transcripts into a single string, separated by newlines
    video_transcripts_combined = ""
    for i in range(len(filtered_videos)):
        video_transcripts_combined += video_transcript_template.format(
            video_creation_date=filtered_videos.loc[i, "createTimeISO"],
            video_text=filtered_videos.loc[i, "text"],
            num_likes=filtered_videos.loc[i, "diggCount"],
            num_shares=filtered_videos.loc[i, "shareCount"],
            view_count=filtered_videos.loc[i, "playCount"],
            num_saves=filtered_videos.loc[i, "collectCount"],
            num_comments=filtered_videos.loc[i, "commentCount"],
            total_engagement_over_num_views=calculate_video_engagement(
                filtered_videos.loc[i, :]
            ),
            mentions=extract_mentions(filtered_videos.loc[i, "detailedMentions"]),
            hashtags=extract_hashtags(filtered_videos.loc[i, "hashtags"]),
            is_sponsored=filtered_videos.loc[i, "isSponsored"],
            is_advertisement=filtered_videos.loc[i, "isAd"],
            video_transcript=filtered_videos.loc[i, "video_transcript"],
        )

    return video_transcripts_combined
