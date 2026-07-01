import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
import joblib

# Load the dataset
data = pd.read_csv("commands.csv")

# Features and labels
X = data["text"]
y = data["intent"]

# Create the model
model = Pipeline([
    ("tfidf", TfidfVectorizer()),
    ("classifier", MultinomialNB())
])

# Train the model
model.fit(X, y)

# Save the trained model
joblib.dump(model, "sand_model.pkl")

print("Model trained successfully!")
