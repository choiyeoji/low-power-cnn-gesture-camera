`timescale 1ns / 1ps

module ds_128 #(
    parameter IN_W = 1280,
    parameter IN_H = 720,
    parameter CROP = 512,
    parameter DEC  = 4
) (
    input wire aclk,
    input wire aresetn,

    input  wire [23:0] s_axis_tdata,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,
    input  wire        s_axis_tlast,
    input  wire        s_axis_tuser,

    output reg  [7:0] m_axis_tdata,
    output reg        m_axis_tvalid,
    input  wire       m_axis_tready,
    output reg        m_axis_tlast,
    output reg        m_axis_tuser
);

    localparam [11:0] X0 = (IN_W - CROP) / 2;
    localparam [11:0] Y0 = (IN_H - CROP) / 2;
    localparam [11:0] X1 = (IN_W - CROP) / 2 + CROP;
    localparam [11:0] Y1 = (IN_H - CROP) / 2 + CROP;
    localparam [11:0] XL = (IN_W - CROP) / 2 + CROP - DEC;
    localparam [11:0] DECM = DEC - 1;

    reg  [11:0] x_q;
    reg  [11:0] y_q;

    wire [11:0] cx = s_axis_tuser ? 12'd0 : x_q;
    wire [11:0] cy = s_axis_tuser ? 12'd0 : y_q;

    assign s_axis_tready = 1'b1;

    wire [11:0] rel_x = cx - X0;
    wire [11:0] rel_y = cy - Y0;

    wire in_x = (cx >= X0) && (cx < X1);
    wire in_y = (cy >= Y0) && (cy < Y1);
    wire grid_x = ((rel_x & DECM) == 12'd0);
    wire grid_y = ((rel_y & DECM) == 12'd0);

    wire take = s_axis_tvalid && in_x && in_y && grid_x && grid_y;

    always @(posedge aclk) begin
        if (!aresetn) begin
            x_q <= 12'd0;
            y_q <= 12'd0;
        end else if (s_axis_tvalid) begin
            if (s_axis_tlast) begin
                x_q <= 12'd0;
                y_q <= cy + 12'd1;
            end else begin
                x_q <= cx + 12'd1;
                y_q <= cy;
            end
        end
    end

    always @(posedge aclk) begin
        if (!aresetn) begin
            m_axis_tdata  <= 8'd0;
            m_axis_tvalid <= 1'b0;
            m_axis_tlast  <= 1'b0;
            m_axis_tuser  <= 1'b0;
        end else begin
            if (take) begin
                m_axis_tdata  <= s_axis_tdata[7:0];
                m_axis_tvalid <= 1'b1;
                m_axis_tlast  <= (cx == XL);
                m_axis_tuser  <= (cx == X0) && (cy == Y0);
            end else if (m_axis_tready) begin
                m_axis_tvalid <= 1'b0;
            end
        end
    end

endmodule
