import json
import pandas as pd
import streamlit as st
import requests
from datetime import datetime
from fpdf import FPDF
from textblob import TextBlob
import io
import folium
from streamlit_folium import st_folium
from cachetools import cached, TTLCache

# Google Places API Key
API_KEY = st.secrets["API_KEY"]

# Load JSON data
JSON_PATH = "dealers_data.json"
try:
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        dealers_data = json.load(f)
except FileNotFoundError:
    st.error(f"The file {JSON_PATH} was not found. Please ensure it is in the same directory as this script.")
    st.stop()

# Convert JSON to DataFrame
data = []
for dealer in dealers_data:
    data.append({
        "Dealer": dealer["actual_name"],
        "Rating": dealer["overall_rating"],
        "Total Reviews": dealer["total_reviews"],
        "Province": dealer.get("province", "Unknown"),
        "PostalCode": dealer.get("postal_code", None),
        "Latitude": dealer.get("latitude", None),
        "Longitude": dealer.get("longitude", None),
        "ReviewTime": [review.get("time") for review in dealer.get("reviews", [])] if dealer.get("reviews") else []
    })
df = pd.DataFrame(data)

# Geocode Postal Codes with Caching
cache = TTLCache(maxsize=100, ttl=86400)  # Cache geocoding results for 1 day

@cached(cache)
def geocode_postal_code(postal_code):
    """Convert a postal code to latitude and longitude using Google Maps Geocoding API."""
    geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": postal_code,
        "key": API_KEY
    }
    response = requests.get(geocode_url, params=params).json()

    if response["status"] == "OK" and response["results"]:
        location = response["results"][0]["geometry"]["location"]
        return location["lat"], location["lng"]
    else:
        return None, None

# Add geocoded coordinates if missing
for index, row in df.iterrows():
    if pd.isna(row["Latitude"]):
        lat, lng = geocode_postal_code(row.get("PostalCode", ""))
        df.at[index, "Latitude"] = lat
        df.at[index, "Longitude"] = lng

# Fetch Live Reviews
def fetch_live_reviews(dealers):
    """Fetch live reviews and ratings for dealers using Google Places API."""
    updated_dealers = []

    for dealer in dealers:
        place_id = dealer.get("place_id")  # Assuming place_id exists in the dataset
        if not place_id:
            dealer["Status"] = "Failed: No Place ID"
            updated_dealers.append(dealer)
            continue

        # API call to fetch details
        url = f"https://maps.googleapis.com/maps/api/place/details/json"
        params = {
            "place_id": place_id,
            "fields": "name,rating,user_ratings_total,reviews",
            "key": API_KEY,
        }

        response = requests.get(url, params=params)
        if response.status_code == 200:
            result = response.json().get("result", {})
            if result:
                dealer["Rating"] = result.get("rating")
                dealer["Total Reviews"] = result.get("user_ratings_total")
                dealer["Reviews"] = result.get("reviews", [])
                dealer["Status"] = "Updated"
            else:
                dealer["Status"] = "Failed: No Results"
        else:
            dealer["Status"] = f"Failed: {response.status_code}"

        updated_dealers.append(dealer)

    return updated_dealers

# Analyze Sentiment
def analyze_sentiment(reviews):
    sentiment_summary = {"Positive": 0, "Neutral": 0, "Negative": 0}
    for review in reviews:
        polarity = TextBlob(review).sentiment.polarity
        if polarity > 0.2:
            sentiment_summary["Positive"] += 1
        elif polarity < -0.2:
            sentiment_summary["Negative"] += 1
        else:
            sentiment_summary["Neutral"] += 1
    return sentiment_summary

# Filter Data
def filter_data(start_date, end_date, provinces, ratings, dealer):
    filtered = df[
        (df["Rating"] >= ratings[0])
        & (df["Rating"] <= ratings[1])
        & (df["Province"].isin(provinces))
    ]
    if dealer != "All Dealers":
        filtered = filtered[filtered["Dealer"] == dealer]
    return filtered

# Streamlit App
st.set_page_config(page_title="VW Google Reviews", layout="wide")
st.title("VW Google Reviews")
st.markdown("## National, Dealer, Sales & Aftersales Insights")

# Sidebar Filters
st.sidebar.subheader("Filters")
start_date = st.sidebar.date_input("Start Date", value=datetime(2022, 1, 1))
end_date = st.sidebar.date_input("End Date", value=datetime.now())
province_filter = st.sidebar.multiselect("Select Province(s)", options=df["Province"].dropna().unique(), default=df["Province"].dropna().unique())
dealer_filter = st.sidebar.selectbox("Select Dealer", ["All Dealers"] + df[df["Province"].isin(province_filter)]["Dealer"].tolist())
rating_range = st.sidebar.slider("Rating Range", min_value=1.0, max_value=5.0, value=(1.0, 5.0))

# Filtered Data
filtered_df = filter_data(start_date, end_date, province_filter, rating_range, dealer_filter)

# Tabs for different analyses
tabs = st.tabs(["National Overview", "Dealer Insights", "Review Trends", "Dealer Map", "Refresh Data"])

# National Overview
def national_overview():
    st.subheader("National Overview")

    # Key Metrics
    col1, col2, col3 = st.columns(3)
    col1.metric(label="Total Dealers", value=len(filtered_df))
    col2.metric(label="Average Rating", value=f"{filtered_df['Rating'].mean():.2f}")
    col3.metric(label="Total Reviews", value=filtered_df["Total Reviews"].sum())

    # Rating Distribution
    st.subheader("Rating Distribution")
    st.bar_chart(filtered_df["Rating"].value_counts().sort_index())

# Dealer Insights
def dealer_insights():
    st.subheader("Dealer Insights")

    if dealer_filter != "All Dealers":
        dealer_data = filtered_df[filtered_df["Dealer"] == dealer_filter]
        st.write(f"### Details for {dealer_filter}")
        st.write(f"**Rating**: {dealer_data['Rating'].values[0]:.2f}")
        st.write(f"**Total Reviews**: {dealer_data['Total Reviews'].values[0]}")

        reviews = [
            review["text"] for dealer in dealers_data if dealer["actual_name"] == dealer_filter
            for review in dealer.get("reviews", [])
        ]
        sentiments = analyze_sentiment(reviews)
        st.write("### Sentiment Analysis")
        st.bar_chart(sentiments)

        st.write("### Reviews")
        for review in reviews[:5]:
            st.write(f"- {review}")

# Review Trends
def review_trends():
    st.subheader("Review Trends Over Time")

    review_times = []
    for review_list in filtered_df["ReviewTime"]:
        if review_list:
            review_times.extend(review_list)

    if review_times:
        # Convert Unix timestamps to datetime and group by month
        review_dates = pd.to_datetime(review_times, unit="s")
        review_counts = review_dates.to_series().dt.to_period("M").value_counts().sort_index()
        st.line_chart(review_counts)
    else:
        st.write("No review trends available for the selected filters.")

# Dealer Map
def dealer_map():
    st.subheader("Dealer Locations with Ratings")
    map_center = [56.1304, -106.3468]  # Canada center coordinates
    dealer_map = folium.Map(location=map_center, zoom_start=4)

    for _, row in filtered_df.iterrows():
        if pd.notna(row["Latitude"]) and pd.notna(row["Longitude"]):
            folium.Marker(
                location=[row["Latitude"], row["Longitude"]],
                popup=f"{row['Dealer']}: {row['Rating']} stars",
                icon=folium.Icon(color="blue" if row["Rating"] >= 4 else "red")
            ).add_to(dealer_map)

    st_folium(dealer_map, width=700)
