import streamlit as st
import pandas as pd
import os
from io import StringIO
from main import ask_ai
import asyncio
import shutil

# Function to process and save the uploaded file
def process_and_save_file(uploaded_file):
    if not os.path.exists("uploads"):
        os.makedirs("uploads")

    # Save the uploaded file to the 'uploads' folder
    file_path = os.path.join("uploads", uploaded_file.name)
    
    # Write the file to the path
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    
    # Return the file path
    return file_path

# Function to process the uploaded file
def process_file(uploaded_file):
    # Read the uploaded file (assuming it's a CSV file)
    if uploaded_file is not None:
        # Convert the uploaded file into a pandas dataframe (assuming CSV format)
        df = pd.read_csv(uploaded_file)
        # Perform any processing you want here. For example, return the dataframe.
        return df

# Create the Streamlit interface
def main():
    st.title('Document Upload, Save, and Output Display')

    uploaded_file = st.file_uploader("Choose a file", type=["pdf"])

    if uploaded_file is not None:
        file_path = process_and_save_file(uploaded_file)
        
        st.write(f"File has been saved to: {file_path}")

        ans = asyncio.run(ask_ai(file_path))
        st.write(ans)
        shutil.rmtree('uploads')


        
        # Display the uploaded file content as a DataFrame (or modify it based on your function)
        # st.write("File content:")
        # st.write(df)
        
        # You can also perform additional operations or outputs here, like showing a summary
        # st.write("Summary of DataFrame:")
        # st.write(df.describe())  # Example of a summary function

if __name__ == "__main__":
    main()
