import pandas as pd
import os
import ast
import yt_dlp
import time
import json
import re
from pydub import AudioSegment
from apify_client import ApifyClient
from openai import OpenAI
from prompts.prompt_template import (
    finfluencer_identification_system_prompt,
    video_transcript_template,
    finfluencer_identification_user_prompt,
    portfoliomanager_reflection_system_prompt,
    portfoliomanager_reflection_user_prompt,
    investmentadvisor_reflection_system_prompt,
    investmentadvisor_reflection_user_prompt,
    financialanalyst_reflection_system_prompt,
    financialanalyst_reflection_user_prompt,
    economist_reflection_system_prompt,
    economist_reflection_user_prompt,
    interview_system_prompt,
    interview_user_prompt,
    profile_prompt_template,
    entity_geographic_inclusion_system_prompt,
    entity_geographic_inclusion_user_prompt,
    polling_system_prompt,
    polling_user_prompt,
)
from config.base_config import *
from config.market_signals_config import (
    RUSSELL_4000_STOCK_TICKER_FILE,
)


openai_client = OpenAI(api_key=OPENAI_API_KEY)
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
    project_name: str,
    video_metadata_file: str,
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

    # Extract profile id information
    video_metadata["profile_id"] = video_metadata["authorMeta"].apply(
        lambda x: x.get("id", None) if isinstance(x, dict) else None
    )

    # Define the file path
    video_metadata_path = f"{base_dir}/../data/{project_name}/{video_metadata_file}"

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


def update_profile_metadata(
    project_name: str, profile_metadata_file: str, video_metadata_file: str
) -> None:
    """
    Updates the profile metadata for a given project by processing the video metadata.

    Args:
        profile_search (bool): A boolean indicating whether the search was for profiles or not.
    """
    # Load video metadata file
    video_metadata_path = f"{base_dir}/../data/{project_name}/{video_metadata_file}"
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
    profile_metadata_path = f"{base_dir}/../data/{project_name}/{profile_metadata_file}"
    profile_metadata.to_csv(profile_metadata_path, index=False)

    return None


def identify_top_influencers(
    top_n_profiles: int, project_name: str, profile_metadata_file: str
) -> None:
    """
    Identifies the top N influencers based on the number of followers from a profile metadata file
    and saves their profiles to a text file.

    Args:
        top_n_profiles (int): The number of top profiles to identify based on the number of followers.

    Returns:
        None
    """
    # Load profile metadata file based on keyword search
    profile_metadata_path = f"{base_dir}/../data/{project_name}/{profile_metadata_file}"
    profile_metadata = pd.read_csv(profile_metadata_path)

    # Sort profiles based on number of followers
    profile_metadata_sorted = profile_metadata.sort_values(
        by="fans", ascending=False
    ).reset_index(drop=True)

    # Identify top n profiles based on number of followers
    profile_metadata_top_n_profiles = profile_metadata_sorted.head(top_n_profiles)

    # Save top n profiles to a text file
    profiles = profile_metadata_top_n_profiles["profile"].tolist()
    profiles_path = f"{base_dir}/../config/{project_name}_profiles.txt"

    with open(profiles_path, "w") as file:
        for profile in profiles:
            file.write(f"{profile}\n")

    return None


def download_video(row: pd.Series, project_name: str) -> None:
    """
    Downloads a TikTok video using the provided information in the row.

    Args:
        row (pd.Series): A pandas Series containing the video information, including the 'webVideoUrl' and 'video_filename'.
        project_name (str): The project name used to construct the output file path.

    Returns:
        None
    """
    # The TikTok video link
    video_url = row["webVideoUrl"]

    # Output file name
    output_file = (
        f"{base_dir}/../data/{project_name}/video-downloads/{row['video_filename']}"
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


def transcribe_videos(row: pd.Series, project_name: str) -> str:
    """
    Transcribes the audio from a video file using the OpenAI Whisper model.
    Args:
        row (pd.Series): A pandas Series containing information about the video file.
                         It must include a 'video_filename' key with the name of the video file.
        project_name (str): The name of the project, used to construct the file paths.
    Returns:
        str: The transcription of the audio if successful, otherwise None.
    Raises:
        FileNotFoundError: If the input video file is not found.
        Exception: For other errors encountered during transcription, including file size issues.
    """
    input_file_path = (
        f"{base_dir}/../data/{project_name}/video-downloads/{row['video_filename']}"
    )
    optimized_file_path = f"{base_dir}/../data/{project_name}/video-downloads/optimized_{row['video_filename'][:-4] + '.wav'}"

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


def construct_system_prompt(row: pd.Series, interview_type: str) -> str:
    if interview_type == "finfluencer_identification":
        system_prompt_template = finfluencer_identification_system_prompt

    elif interview_type == "portfoliomanager_reflection":
        system_prompt_template = portfoliomanager_reflection_system_prompt

    elif interview_type == "investmentadvisor_reflection":
        system_prompt_template = investmentadvisor_reflection_system_prompt

    elif interview_type == "financialanalyst_reflection":
        system_prompt_template = financialanalyst_reflection_system_prompt

    elif interview_type == "economist_reflection":
        system_prompt_template = economist_reflection_system_prompt

    elif interview_type == "interview":
        system_prompt_template = interview_system_prompt

    elif interview_type == "entity_geographic_inclusion":
        system_prompt = entity_geographic_inclusion_system_prompt.format(
            profile_prompt=row["profile_prompt"]
        )
        return system_prompt

    elif interview_type == "polling":
        system_prompt = polling_system_prompt.format(
            profile_prompt=row["profile_prompt"]
        )
        return system_prompt

    else:
        raise ValueError(f"Interview Type {interview_type} is not supported.")

    if interview_type == "interview":
        system_prompt = system_prompt_template.format(
            expert_reflection_portfoliomanager=row[
                "expert_reflection_portfoliomanager"
            ],
            expert_reflection_investmentadvisor=row[
                "expert_reflection_investmentadvisor"
            ],
            expert_reflection_financialanalyst=row[
                "expert_reflection_financialanalyst"
            ],
            expert_reflection_economist=row["expert_reflection_economist"],
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

    else:
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


def construct_user_prompt(row: pd.Series, interview_type: str) -> str:
    if interview_type == "finfluencer_identification":
        return finfluencer_identification_user_prompt

    elif interview_type == "portfoliomanager_reflection":
        return portfoliomanager_reflection_user_prompt

    elif interview_type == "investmentadvisor_reflection":
        return investmentadvisor_reflection_user_prompt

    elif interview_type == "financialanalyst_reflection":
        return financialanalyst_reflection_user_prompt

    elif interview_type == "economist_reflection":
        return economist_reflection_user_prompt

    elif interview_type == "entity_geographic_inclusion":
        return entity_geographic_inclusion_user_prompt

    elif interview_type == "polling":
        return polling_user_prompt

    elif interview_type == "interview":
        # Load Russell 4000 stock tickers
        full_file_path = f"{base_dir}/../config/{RUSSELL_4000_STOCK_TICKER_FILE}"
        russell4000_stock_tickers = pd.read_csv(full_file_path)

        # Construct Russell 4000 stock ticker string
        russell4000_stock_tickers["combined_ticker"] = russell4000_stock_tickers.apply(
            lambda stock_row: f"{stock_row['COMNAM']} ({stock_row['TICKER']})", axis=1
        )
        russell4000_stock_ticker_list = russell4000_stock_tickers[
            "combined_ticker"
        ].to_list()
        russell4000_stock_ticker_str = ", ".join(russell4000_stock_ticker_list)

        # Construct user prompt
        return interview_user_prompt.format(
            russell_4000_tickers=russell4000_stock_ticker_str,
            stock_mentions=row["stock_mentions"],
        )

    else:
        raise ValueError(f"Interview Type {interview_type} is not supported.")


def extract_llm_responses(text, substring_exclusion_list: list = []) -> pd.Series:
    # Split the text by double newlines to separate different questions
    questions_blocks = text.split("\n\n")
    questions_blocks = [
        block
        for block in questions_blocks
        if not any(substring in block for substring in substring_exclusion_list)
    ]  # remove blocks containing stock recommendations

    # Initialize lists to store the extracted data
    questions_list = []
    explanations_list = []
    symbols_list = []
    categories_list = []
    speculations_list = []
    values_list = []
    response_list = []

    # Define regex patterns for each field
    question_pattern = r"\*\*question: (.*?)\*\*"
    explanation_pattern = r"\*\*explanation: (.*?)\*\*"
    symbol_pattern = r"\*\*symbol: (.*?)\*\*"
    category_pattern = r"\*\*category: (.*?)\*\*"
    speculation_pattern = r"\*\*speculation: (.*?)\*\*"
    value_pattern = r"\*\*value: (.*?)\*\*"
    response_pattern = r"\*\*response: (.*?)\*\*"

    # Iterate through each question block and extract the fields
    for block in questions_blocks:
        question = re.search(question_pattern, block, re.DOTALL)
        explanation = re.search(explanation_pattern, block, re.DOTALL)
        symbol = re.search(symbol_pattern, block, re.DOTALL)
        category = re.search(category_pattern, block, re.DOTALL)
        speculation = re.search(speculation_pattern, block, re.DOTALL)
        value = re.search(value_pattern, block, re.DOTALL)
        response = re.search(response_pattern, block, re.DOTALL)

        questions_list.append(question.group(1).replace("”", "") if question else None)
        explanations_list.append(explanation.group(1) if explanation else None)
        symbols_list.append(symbol.group(1) if symbol else None)
        categories_list.append(category.group(1) if category else None)
        speculations_list.append(speculation.group(1) if speculation else None)
        values_list.append(value.group(1) if value else None)
        response_list.append(response.group(1) if response else None)

    # Create a DataFrame
    data = {
        "question": questions_list,
        "explanation": explanations_list,
        "symbol": symbols_list,
        "category": categories_list,
        "speculation": speculations_list,
        "value": values_list,
        "response": response_list,
    }
    df = pd.DataFrame(data)

    # Flatten the DataFrame into a single Series
    flattened_series = pd.Series()
    for index, row in df.iterrows():
        question_prefix = row["question"]
        if row["explanation"]:
            flattened_series[f"{question_prefix} - explanation"] = row["explanation"]
        if row["symbol"]:
            flattened_series[f"{question_prefix} - symbol"] = row["symbol"]
        if row["category"]:
            flattened_series[f"{question_prefix} - category"] = row["category"]
        if row["speculation"]:
            flattened_series[f"{question_prefix} - speculation"] = row["speculation"]
        if row["value"]:
            flattened_series[f"{question_prefix} - value"] = row["value"]
        if row["response"]:
            flattened_series[f"{question_prefix} - response"] = row["response"]

    return flattened_series


def extract_stock_recommendations(
    row: pd.Series, llm_response_field: str
) -> pd.DataFrame:
    # Split the text by double newlines to separate different stock recommendations
    questions_blocks = row[llm_response_field].split("\n\n")
    questions_blocks = [
        block for block in questions_blocks if "stock name" in block
    ]  # remove blocks containing stock recommendations

    # Initialize lists to store the extracted data
    stock_name_list = []
    stock_ticker_list = []
    mention_date_list = []
    mentioned_by_influencer_list = []
    recommendation_list = []
    explanation_list = []
    confidence_list = []
    virality_list = []

    # Define regex patterns for each field
    stock_name_pattern = r"\*\*stock name: (.*?)\*\*"
    stock_ticker_pattern = r"\*\*stock ticker: (.*?)\*\*"
    mention_date_pattern = r"\*\*mention date: (.*?)\*\*"
    mentioned_by_influencer_pattern = r"\*\*mentioned by influencer: (.*?)\*\*"
    recommendation_pattern = r"\*\*recommendation: (.*?)\*\*"
    explanation_pattern = r"\*\*explanation: (.*?)\*\*"
    confidence_pattern = r"\*\*confidence: (.*?)\*\*"
    virality_pattern = r"\*\*virality: (.*?)\*\*"

    # Iterate through each question block and extract the fields
    for block in questions_blocks:
        stock_name = re.search(stock_name_pattern, block, re.DOTALL)
        stock_ticker = re.search(stock_ticker_pattern, block, re.DOTALL)
        mention_date = re.search(mention_date_pattern, block, re.DOTALL)
        mentioned_by_influencer = re.search(
            mentioned_by_influencer_pattern, block, re.DOTALL
        )
        recommendation = re.search(recommendation_pattern, block, re.DOTALL)
        explanation = re.search(explanation_pattern, block, re.DOTALL)
        confidence = re.search(confidence_pattern, block, re.DOTALL)
        virality = re.search(virality_pattern, block, re.DOTALL)

        stock_name_list.append(stock_name.group(1) if stock_name else None)
        stock_ticker_list.append(stock_ticker.group(1) if stock_ticker else None)
        mention_date_list.append(mention_date.group(1) if mention_date else None)
        mentioned_by_influencer_list.append(
            mentioned_by_influencer.group(1) if mentioned_by_influencer else None
        )
        recommendation_list.append(recommendation.group(1) if recommendation else None)
        explanation_list.append(explanation.group(1) if explanation else None)
        confidence_list.append(confidence.group(1) if confidence else None)
        virality_list.append(virality.group(1) if virality else None)

    # Create a DataFrame
    data = {
        "stock_name": stock_name_list,
        "stock_ticker": stock_ticker_list,
        "mention date": mention_date_list,
        "mentioned by influencer": mentioned_by_influencer_list,
        "recommendation": recommendation_list,
        "explanation": explanation_list,
        "confidence": confidence_list,
        "virality": virality_list,
    }
    df = pd.DataFrame(data)

    return df


def create_batch_file(
    prompts: pd.DataFrame,
    project_name: str,
    gpt_model: str,
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
                "model": gpt_model,
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
        f"{base_dir}/../data/{project_name}/batch-files/{batch_file_name}", "w"
    ) as file:
        for obj in tasks:
            file.write(json.dumps(obj) + "\n")

    return batch_file_name


def batch_query(
    project_name: str,
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
    # Upload batch input file
    batch_file = openai_client.files.create(
        file=open(
            f"{base_dir}/../data/{project_name}/batch-files/{batch_input_file_dir}",
            "rb",
        ),
        purpose="batch",
    )

    # Create batch job
    batch_job = openai_client.batches.create(
        input_file_id=batch_file.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )

    # Check batch status
    while True:
        batch_job = openai_client.batches.retrieve(batch_job.id)
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
    results = openai_client.files.content(result_file_id).content

    # Save the batch output
    with open(
        f"{base_dir}/../data/{project_name}/batch-files/{batch_output_file_dir}", "wb"
    ) as file:
        file.write(results)

    # Loading data from saved output file
    response_list = []
    with open(
        f"{base_dir}/../data/{project_name}/batch-files/{batch_output_file_dir}", "r"
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
            video_text=(
                filtered_videos.loc[i, "text"].replace("\n", " ")
                if not pd.isnull(filtered_videos.loc[i, "text"])
                else ""
            ),
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


def row_query(row: pd.Series, args: list) -> str:
    system_prompt = row[args[0]]
    user_prompt = row[args[1]]
    gpt_model = row[args[2]]

    # Skip if system_prompt/user_prompt is empty or NaN (depending on your logic)
    if not isinstance(system_prompt, str) or not isinstance(user_prompt, str):
        return ""

    # Make a chat completion request
    try:
        response = openai_client.chat.completions.create(
            model=gpt_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )

        # Extract the assistant's response
        return response.choices[0].message.content

    except Exception as e:
        # Handle errors (rate limits, etc.)
        print(f"Error processing row: {e}")
        return "Error or Timeout"


def perform_profile_interview(
    project_name: str,
    gpt_model: str,
    profile_metadata_file: str,
    video_metadata_file: str,
    output_file: str,
    system_prompt_field: str,
    user_prompt_field: str,
    llm_response_field: str,
    interview_type: str,
    batch_interview: bool = True,
) -> None:

    # Load profile and video metadata
    print("Loading profile and video metadata...")
    profile_metadata = pd.read_csv(
        f"{base_dir}/../data/{project_name}/{profile_metadata_file}"
    )
    video_metadata = pd.read_csv(
        f"{base_dir}/../data/{project_name}/{video_metadata_file}"
    )
    video_metadata["createTimeISO"] = pd.to_datetime(video_metadata["createTimeISO"])

    # Preprocess profile and video metadata
    print("Preprocess profile and video metadata...")
    video_metadata["profile_id"] = video_metadata["authorMeta"].apply(
        extract_profile_id
    )
    video_metadata["profile_id"] = video_metadata["profile_id"].astype(str)
    profile_metadata["id"] = profile_metadata["id"].astype(str)

    # Generate system and user prompts
    print("Generate system and user prompts...")
    profile_metadata["transcripts_combined"] = profile_metadata["id"].apply(
        extract_video_transcripts, args=(video_metadata,)
    )

    profile_metadata[system_prompt_field] = profile_metadata.apply(
        construct_system_prompt, args=(interview_type,), axis=1
    )
    profile_metadata[user_prompt_field] = profile_metadata.apply(
        construct_user_prompt, args=(interview_type,), axis=1
    )

    if batch_interview:
        # Generate custom ids
        if "custom_id" not in profile_metadata.columns:
            profile_metadata = profile_metadata.reset_index(drop=False)
            profile_metadata.rename(columns={"index": "custom_id"}, inplace=True)

        # Create folder to contain batch files
        batch_file_dir = f"{base_dir}/../data/{project_name}/batch-files"
        os.makedirs(batch_file_dir, exist_ok=True)

        # Perform batch query for survey questions
        batch_file_dir = create_batch_file(
            profile_metadata,
            project_name=project_name,
            gpt_model=gpt_model,
            system_prompt_field=system_prompt_field,
            user_prompt_field=user_prompt_field,
            batch_file_name="batch_input.jsonl",
        )

        print("Perform batch query using OpenAI API...")
        llm_responses = batch_query(
            project_name=project_name,
            batch_input_file_dir="batch_input.jsonl",
            batch_output_file_dir="batch_output.jsonl",
        )
        llm_responses.rename(
            columns={"query_response": llm_response_field}, inplace=True
        )

        # Merge LLM response with original dataset
        print("Merge LLM response with original dataset...")
        profile_metadata["custom_id"] = profile_metadata["custom_id"].astype("int64")
        llm_responses["custom_id"] = llm_responses["custom_id"].astype("int64")
        profile_metadata_with_responses = pd.merge(
            left=profile_metadata,
            right=llm_responses[["custom_id", llm_response_field]],
            on="custom_id",
        )

        # Save profile metadata after analysis into CSV file
        print("Saving profile metadata with analysis...")
        profile_metadata_with_responses.to_csv(
            f"{base_dir}/../data/{project_name}/{output_file}", index=False
        )

    else:
        print("Querying the OpenAI Chat Completion API (one row at a time)...")
        profile_metadata[llm_response_field] = profile_metadata.progress_apply(
            row_query,
            args=([system_prompt_field, user_prompt_field, gpt_model],),
            axis=1,
        )

        # Save profile metadata after analysis into CSV file
        print("Saving profile metadata with analysis...")
        profile_metadata.to_csv(
            f"{base_dir}/../data/{project_name}/{output_file}", index=False
        )


def perform_profile_interview_shorten(
    project_name: str,
    gpt_model: str,
    profile_metadata_input_file: str,
    profile_metadata_output_file: str,
    system_prompt_field: str,
    user_prompt_field: str,
    llm_response_field: str,
    interview_type: str,
    batch_interview: bool = True,
) -> None:

    print("Loading profile metadata...")
    profile_metadata = pd.read_csv(
        f"{base_dir}/../data/{project_name}/{profile_metadata_input_file}"
    )

    print("Generate system and user prompts...")
    profile_metadata[system_prompt_field] = profile_metadata.apply(
        construct_system_prompt, args=(interview_type,), axis=1
    )
    profile_metadata[user_prompt_field] = profile_metadata.apply(
        construct_user_prompt, args=(interview_type,), axis=1
    )

    if batch_interview:
        # Generate custom ids
        if "custom_id" not in profile_metadata.columns:
            profile_metadata = profile_metadata.reset_index(drop=False)
            profile_metadata.rename(columns={"index": "custom_id"}, inplace=True)

        # Create folder to contain batch files
        batch_file_dir = f"{base_dir}/../data/{project_name}/batch-files"
        os.makedirs(batch_file_dir, exist_ok=True)

        # Perform batch query for survey questions
        batch_file_dir = create_batch_file(
            profile_metadata,
            project_name=project_name,
            gpt_model=gpt_model,
            system_prompt_field=system_prompt_field,
            user_prompt_field=user_prompt_field,
            batch_file_name="batch_input.jsonl",
        )

        print("Perform batch query using OpenAI API...")
        llm_responses = batch_query(
            project_name=project_name,
            batch_input_file_dir="batch_input.jsonl",
            batch_output_file_dir="batch_output.jsonl",
        )
        llm_responses.rename(
            columns={"query_response": llm_response_field}, inplace=True
        )

        # Merge LLM response with original dataset
        print("Merge LLM response with original dataset...")
        profile_metadata["custom_id"] = profile_metadata["custom_id"].astype("int64")
        llm_responses["custom_id"] = llm_responses["custom_id"].astype("int64")
        profile_metadata_with_responses = pd.merge(
            left=profile_metadata,
            right=llm_responses[["custom_id", llm_response_field]],
            on="custom_id",
        )

        # Save profile metadata after analysis into CSV file
        print("Saving profile metadata after interview...")
        profile_metadata_with_responses.to_csv(
            f"{base_dir}/../data/{project_name}/{profile_metadata_output_file}",
            index=False,
        )

    else:
        print("Querying the OpenAI Chat Completion API (one row at a time)...")
        profile_metadata[llm_response_field] = profile_metadata.progress_apply(
            row_query,
            args=([system_prompt_field, user_prompt_field, gpt_model],),
            axis=1,
        )

        # Save profile metadata after analysis into CSV file
        print("Saving profile metadata after interview...")
        profile_metadata.to_csv(
            f"{base_dir}/../data/{project_name}/{profile_metadata_output_file}",
            index=False,
        )


def build_profile_prompt(
    project_name: str,
    profile_metadata_input_file: str,
    profile_metadata_output_file: str,
    video_metadata_file: str,
) -> None:
    # Load profile and video metadata
    print("Loading profile and video metadata...")
    profile_metadata = pd.read_csv(
        f"{base_dir}/../data/{project_name}/{profile_metadata_input_file}"
    )
    video_metadata = pd.read_csv(
        f"{base_dir}/../data/{project_name}/{video_metadata_file}"
    )
    video_metadata["createTimeISO"] = pd.to_datetime(video_metadata["createTimeISO"])

    # Preprocess profile and video metadata
    print("Preprocess profile and video metadata...")
    video_metadata["profile_id"] = video_metadata["authorMeta"].apply(
        extract_profile_id
    )
    video_metadata["profile_id"] = video_metadata["profile_id"].astype(str)
    profile_metadata["id"] = profile_metadata["id"].astype(str)

    # Construct past transcripts
    print("Construct past transcripts...")
    profile_metadata["transcripts_combined"] = profile_metadata["id"].apply(
        extract_video_transcripts, args=(video_metadata,)
    )

    # Construct profile prompt
    print("Construct profile prompt...")
    profile_metadata["profile_prompt"] = profile_metadata.apply(
        lambda row: profile_prompt_template.format(
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
        ),
        axis=1,
    )

    # Save updated profile metadata
    profile_metadata.to_csv(
        f"{base_dir}/../data/{project_name}/{profile_metadata_output_file}", index=False
    )

    return None
