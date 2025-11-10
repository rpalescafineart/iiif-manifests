The python scripts automate several interrelated tasks within the IIIF publishing pipeline:
	•	Parsing structured metadata from CSV source files.
	•	Creating individual manifest .json files for each artwork, conforming to IIIF Presentation API v3.
	•	Embedding descriptive and rights metadata, linked image resources, and thumbnail references.
	•	Generating and validating a central collection.json file to aggregate all manifests into a browsable collection.


Code execution follows a simple sequence:
	1.	generate-thumbs.py – downloads and optimizes image thumbnails from source URLs.
	2.	make_manifests_with_thumbs.py – generates item manifests and builds the collection index.

Command-line arguments allow customization of file paths and web prefixes for hosting environments. Validation of the resulting manifests can be performed using any IIIF validator or by loading the collection.json URL directly into the Universal Viewer.
