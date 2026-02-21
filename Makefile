MODULE=module2
IMAGE_REG?=registry.local:5000

.PHONY: build-images
build-images:
	docker build -t $(IMAGE_REG)/vw-gw:0.1.0 services/gateway
	docker build -t $(IMAGE_REG)/vw-compositor:0.1.0 services/compositor

.PHONY: lint
lint:
	python -m compileall services/gateway/app services/compositor/app
