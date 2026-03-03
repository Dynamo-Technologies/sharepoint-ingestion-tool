# ---------------------------------------------------------------
# VPC + Networking for Open WebUI ECS Deployment
# ---------------------------------------------------------------

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "webui" {
  cidr_block           = "10.100.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "sp-ingest-webui" }
}

# --- Public Subnets (ALB) ---

resource "aws_subnet" "webui_public" {
  count                   = 2
  vpc_id                  = aws_vpc.webui.id
  cidr_block              = cidrsubnet(aws_vpc.webui.cidr_block, 8, count.index + 1)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "sp-ingest-webui-public-${count.index}" }
}

# --- Private Subnets (ECS Tasks) ---

resource "aws_subnet" "webui_private" {
  count             = 2
  vpc_id            = aws_vpc.webui.id
  cidr_block        = cidrsubnet(aws_vpc.webui.cidr_block, 8, count.index + 10)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "sp-ingest-webui-private-${count.index}" }
}

# --- Internet Gateway ---

resource "aws_internet_gateway" "webui" {
  vpc_id = aws_vpc.webui.id

  tags = { Name = "sp-ingest-webui-igw" }
}

# --- NAT Gateway (single, in first public subnet) ---

resource "aws_eip" "nat" {
  domain = "vpc"

  tags = { Name = "sp-ingest-webui-nat-eip" }
}

resource "aws_nat_gateway" "webui" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.webui_public[0].id

  tags = { Name = "sp-ingest-webui-nat" }

  depends_on = [aws_internet_gateway.webui]
}

# --- Route Tables ---

resource "aws_route_table" "webui_public" {
  vpc_id = aws_vpc.webui.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.webui.id
  }

  tags = { Name = "sp-ingest-webui-public-rt" }
}

resource "aws_route_table_association" "webui_public" {
  count          = 2
  subnet_id      = aws_subnet.webui_public[count.index].id
  route_table_id = aws_route_table.webui_public.id
}

resource "aws_route_table" "webui_private" {
  vpc_id = aws_vpc.webui.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.webui.id
  }

  tags = { Name = "sp-ingest-webui-private-rt" }
}

resource "aws_route_table_association" "webui_private" {
  count          = 2
  subnet_id      = aws_subnet.webui_private[count.index].id
  route_table_id = aws_route_table.webui_private.id
}

# --- Security Groups ---

resource "aws_security_group" "webui_alb" {
  name_prefix = "sp-ingest-webui-alb-"
  description = "Allow HTTP/HTTPS inbound to ALB"
  vpc_id      = aws_vpc.webui.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTP"
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "sp-ingest-webui-alb-sg" }
}

resource "aws_security_group" "webui_ecs" {
  name_prefix = "sp-ingest-webui-ecs-"
  description = "Allow inbound from ALB only on port 8080"
  vpc_id      = aws_vpc.webui.id

  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.webui_alb.id]
    description     = "Open WebUI from ALB"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "sp-ingest-webui-ecs-sg" }
}
