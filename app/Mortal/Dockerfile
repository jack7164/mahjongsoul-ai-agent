# syntax=docker/dockerfile:1.4-labs
FROM archlinux:base-devel AS libriichi_build

RUN pacman -Syu --noconfirm --needed rust python && pacman -Scc --noconfirm

WORKDIR /src
COPY . .
RUN cargo build --release --manifest-path libriichi/Cargo.toml --lib
RUN find . -name "*.so" -exec cp {} ./libriichi.so \;


FROM archlinux:base

RUN pacman -Syu --noconfirm --needed python python-pip python-pytorch-cuda python-toml python-tqdm && \
pacman -Scc --noconfirm

RUN pip install tensorboard --break-system-packages

ENV PYTHONPATH=/src

WORKDIR /src
COPY --from=libriichi_build /src/libriichi.so ./
COPY mortal ./mortal
COPY *.toml ./

ENTRYPOINT ["python3", "mortal/mortal.py"]
