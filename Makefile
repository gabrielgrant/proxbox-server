build-image:
	docker build -t gabrielgrant/proxbox-server:0.1.5 .
publish-image: build-image
	docker push gabrielgrant/proxbox-server:0.1.5
