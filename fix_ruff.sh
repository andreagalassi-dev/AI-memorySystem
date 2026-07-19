sed -i '' 's/"streamlit>=1.33",//' pyproject.toml
echo 'demo = [\n    "streamlit>=1.33",\n]' >> pyproject.toml
